"""The analysis job itself, independent of what invoked it.

One function, called from two places: the arq worker in production, and
directly by the web process when no queue is configured (local development).
The body is shared rather than duplicated, so the path a developer exercises is
the path production runs -- a second implementation for dev is how "works on my
machine" gets built deliberately.

This is also the containment boundary. Analysing an untrusted repository means
running tree-sitter over attacker-authored files, cloning attacker-named
branches and invoking pip-audit on attacker-supplied requirements. In the
worker that happens in a process holding no session keys and no HTTP surface.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from archguard.analysis.phases import clamp_monotonic, percent_for
from archguard.contract.generation import NoAnalysableModuleError
from archguard.worker import progress

logger = logging.getLogger(__name__)


async def analyse_repository(ctx: dict[str, Any] | None, job_id: str) -> str:
    """Clone and analyse the repository behind *job_id*.

    ``ctx`` is arq's per-job context; it is unused and accepted as ``None`` so
    the same function is callable without a queue. Returns the terminal status,
    which arq records as the job result.

    Never raises. A traceback escaping here would be retried by arq as though
    it were transient, and re-cloning a repository that cannot be analysed just
    burns the worker three more times.
    """
    from archguard.dashboard.pipeline_adapter import run_analysis_on_repo
    from archguard.dashboard.routes.jobs import build_safe_clone_url, parse_github_url
    from archguard.dashboard.workspace import enforce_workspace_budget, temp_workspace
    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.observability.logger import correlation_id_var

    # The job's own id, not the correlation id of the request that queued it.
    # Without this the whole analysis is logged under an HTTP request that
    # returned minutes earlier.
    correlation_id_var.set(job_id[:8])

    # The bar must never go backwards: phases are skipped when the ML extras
    # are absent, and layers 1 and 2 run in a thread pool so they can report
    # out of order. A bar that jumps back reads as a restart.
    percent_seen: dict[str, int | None] = {"value": None}

    async def emit(message: str, phase: str | None = None) -> None:
        percent_seen["value"] = clamp_monotonic(
            percent_seen["value"], percent_for(phase)
        )
        event: dict[str, Any] = {
            "type": "progress",
            "message": f"[{datetime.now(UTC).strftime('%H:%M:%S')}] {message}",
        }
        if phase:
            event["phase"] = phase
        if percent_seen["value"] is not None:
            event["percent"] = percent_seen["value"]
        progress.publish(job_id, event)
        logger.debug("[job %s] %s", job_id, message)

    async def set_status(status: str, error: str | None = None) -> None:
        # Status changes carry a percent too, so the bar moves during the clone
        # -- which on a large repository is a third of the wait.
        try:
            async with session_scope() as session:
                await store.set_job_status(session, job_id, status, error=error)
        except Exception:
            logger.exception("[job %s] Could not record status %s", job_id, status)
        percent_seen["value"] = clamp_monotonic(
            percent_seen["value"], percent_for(status)
        )
        event: dict[str, Any] = {"type": "status", "status": status}
        if percent_seen["value"] is not None:
            event["percent"] = percent_seen["value"]
        progress.publish(job_id, event)

    async with session_scope() as session:
        repo_url = await store.job_repo_url_unscoped(session, job_id)
    if repo_url is None:
        logger.error("[job %s] No such job; nothing to analyse", job_id)
        return "failed"

    try:
        await set_status("cloning")

        # Reclaim disk before adding to it. Only running jobs are protected: a
        # finished job's clone is a cache for browsing results, and the read
        # endpoints already fall back to the persisted run once it is gone.
        evicted, reclaimed = await enforce_workspace_budget(active_job_ids={job_id})
        if evicted:
            logger.info(
                "Evicted %d workspace(s) reclaiming %d bytes before cloning",
                evicted,
                reclaimed,
            )

        await emit(f"Cloning {repo_url}...", "cloning")
        owner, name = parse_github_url(repo_url)
        clone_url = build_safe_clone_url(owner, name)
        token = await _installation_token(owner, name)

        async with temp_workspace(
            clone_url, job_id=job_id, keep_alive=True, token=token
        ) as repo:
            await set_status("analysing")
            # No phase: this is the boundary between the clone and the
            # analysis, and the next real phase is whichever the adapter
            # reports -- contract generation when the repository has no
            # .archguard.yml, scanning when it does. Claiming "scanning" here
            # put the bar past the contract phase before it had run.
            await emit("Repository cloned. Starting analysis...")

            result = await run_analysis_on_repo(
                repo_path=repo,
                job_id=job_id,
                repo_url=repo_url,
                progress_callback=emit,
            )

        await set_status("complete")
        progress.publish(
            job_id, {"type": "result", "result": _result_payload(result)}
        )
        await emit(
            f"Done. Health: {result.health_score:.1f}/100 "
            f"({result.health_grade}) - "
            f"Violations: {result.total_violations} - "
            f"Duration: {result.duration_seconds}s"
        )
        return "complete"

    except TimeoutError as exc:
        await _fail(job_id, set_status, str(exc))
        logger.exception("[job %s] Clone timeout: %s", job_id, exc)
        return "failed"

    except NoAnalysableModuleError as exc:
        # A refusal, not a crash: generation found no module worth measuring
        # and said why. Safe to render verbatim -- the message is static text
        # ArchGuard composed, carrying no filesystem path or internal detail --
        # and it is the only thing that explains why this repository produced
        # no report. Without it the user sees "Analysis failed unexpectedly".
        await _fail(job_id, set_status, str(exc))
        logger.info("[job %s] No analysable module: %s", job_id, exc)
        return "failed"

    except ValueError:
        # parse_github_url. A user input error, so say what is wrong -- but
        # without echoing the input back into the page.
        await _fail(
            job_id,
            set_status,
            "Cannot parse GitHub URL. Expected format: https://github.com/owner/repo",
        )
        logger.warning("[job %s] Malformed GitHub URL rejected", job_id)
        return "failed"

    except Exception as exc:
        # The message is rendered verbatim in the browser, so only text this
        # code composed may go there. An arbitrary exception string carries
        # server filesystem paths, temp directory names and module structure.
        if (
            "git clone failed" in str(exc).lower()
            or getattr(exc, "returncode", None) is not None
        ):
            message = (
                "Repository cloning failed. Ensure the URL is correct, public, "
                "and reachable."
            )
        else:
            message = (
                "Analysis failed unexpectedly. The server logs record the cause "
                f"under job {job_id}."
            )
        await _fail(job_id, set_status, message)
        logger.exception("[job %s] Unexpected failure", job_id)
        return "failed"


async def _installation_token(owner: str, name: str) -> str | None:
    """A GitHub App token for ``owner/name``, when one is available (P3-3).

    ``None`` is the ordinary answer and means "clone anonymously", which is what
    every public repository wants and what every deployment without an App gets.
    A configured App that is simply not installed on this repository lands here
    too: that is a public repository the owner never connected, not a failure.

    Deliberately non-fatal. A private repository will fail at the clone with
    git's own "repository not found", which is the same message an anonymous
    clone of a private repository has always produced -- rather than this
    turning a GitHub API hiccup into a failed job for a public one.
    """
    from archguard.dashboard import _github_app

    if not _github_app.is_configured():
        return None
    try:
        return await _github_app.token_for_repository(owner, name)
    except _github_app.GitHubAppError as exc:
        logger.info("No installation token for %s/%s: %s", owner, name, exc)
        return None
    except Exception:
        # A token is an optimisation for the public case. Never let the App
        # path take down an analysis that would otherwise have succeeded.
        logger.warning(
            "Installation token lookup failed for %s/%s; cloning anonymously",
            owner,
            name,
            exc_info=True,
        )
        return None


async def _fail(job_id: str, set_status: Any, message: str) -> None:
    await set_status("failed", error=message)
    progress.publish(job_id, {"type": "error", "error": message})


def _result_payload(result: Any) -> dict[str, Any]:
    """The subset of the result the stream sends, as plain JSON.

    Explicit rather than ``asdict``: this crosses a process boundary and lands
    in a browser, so what it contains should be a decision rather than whatever
    the dataclass happens to hold today.
    """
    return {
        "job_id": result.job_id,
        "repo_url": result.repo_url,
        "health_score": result.health_score,
        "health_grade": result.health_grade,
        "composite_score": result.composite_score,
        "total_violations": result.total_violations,
        "duration_seconds": result.duration_seconds,
        "skipped": result.skipped,
        "skip_reason": result.skip_reason,
        "error": result.error,
        "contract_auto_generated": result.contract_auto_generated,
        "fallback_directory_heuristic": result.fallback_directory_heuristic,
        "fallback_reason": result.fallback_reason,
        "modules_analyzed": list(result.modules_analyzed or []),
    }
