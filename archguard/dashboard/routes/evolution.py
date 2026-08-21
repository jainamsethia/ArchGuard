"""Git-history evolution analysis endpoints."""

import json
import logging
import threading
from typing import Any, cast

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field

from archguard.dashboard._auth import check_token
from archguard.dashboard._identity import current_user
from archguard.dashboard._rate_limit import rate_limiter
from archguard.dashboard.app import JobIdQuery, app
from archguard.db.models import User

logger = logging.getLogger(__name__)

MIN_RUNS_FOR_HISTORY = 2


async def _repo_scoped_runs(
    job_id: str | None, limit: int, user_id: int
) -> tuple[list[Any], str | None]:
    """Analysis runs belonging to the same repository as *job_id*.

    This is per-repository history now, not a filtered view of one shared log.
    Runs are joined to the repository row, so every scan of a repository is
    correlated regardless of which job produced it or when -- which is what
    makes a trend possible at all. It used to read a workspace log holding
    exactly one run, falling back to a server-wide log that interleaved every
    repository this instance had ever analysed.
    """
    from archguard.db import store
    from archguard.db.session import session_scope

    if job_id is None:
        # No job means no repository context, and answering from every run this
        # instance holds would present a stranger's history as the visitor's.
        return [], None
    job = job_id

    repo_url = None
    try:
        from archguard.dashboard.routes.suppression import repo_url_for_job

        repo_url = await repo_url_for_job(job, user_id)
    except Exception:
        repo_url = None

    async with session_scope() as session:
        if repo_url:
            runs = await store.get_runs_for_repository(
                session, repo_url, user_id, limit=limit
            )
        else:
            # Repository unknown: this job's own runs rather than the whole
            # server's, which would be cross-repo.
            runs = await store.get_runs_for_job(session, job, user_id, limit=limit)
    return list(runs), repo_url


def _insufficient_history(runs: list[Any], repo_url: str | None) -> dict[str, Any]:
    """The honest payload when a repository has too few scans for a trend."""
    return {
        "insufficient_history": True,
        "runs_available": len(runs),
        "runs_required": MIN_RUNS_FOR_HISTORY,
        "repo_url": repo_url,
        "message": (
            "Not enough scan history yet for this repository. "
            f"{len(runs)} scan(s) recorded; at least {MIN_RUNS_FOR_HISTORY} are "
            "needed before changes over time can be shown."
        ),
    }


@app.get(
    "/api/v1/evolution/summary", dependencies=[Depends(check_token), Depends(rate_limiter)]
)
@app.get(
    "/api/evolution/summary", dependencies=[Depends(check_token), Depends(rate_limiter)], deprecated=True
)
async def get_evolution_summary(
    limit: int = Query(default=50, ge=1, le=500),
    job_id: JobIdQuery = None,
    user: User = Depends(current_user),
) -> Any:
    """Return the complete evolution trend report."""
    from archguard.evolution.tracker import EvolutionTracker

    runs, repo_url = await _repo_scoped_runs(job_id, limit, user.id)
    if len(runs) < MIN_RUNS_FOR_HISTORY:
        return _insufficient_history(runs, repo_url)
    tracker = EvolutionTracker(runs)
    report = tracker.generate_report()
    return report.model_dump() if hasattr(report, "model_dump") else report.dict()


@app.get(
    "/api/v1/evolution/history", dependencies=[Depends(check_token), Depends(rate_limiter)]
)
@app.get(
    "/api/evolution/history", dependencies=[Depends(check_token), Depends(rate_limiter)], deprecated=True
)
async def get_evolution_history(
    limit: int = Query(default=50, ge=1, le=500),
    job_id: JobIdQuery = None,
    user: User = Depends(current_user),
) -> Any:
    """Return the parsed evolution snapshots."""
    from archguard.evolution.tracker import EvolutionTracker

    runs, repo_url = await _repo_scoped_runs(job_id, limit, user.id)
    if len(runs) < MIN_RUNS_FOR_HISTORY:
        return _insufficient_history(runs, repo_url)
    tracker = EvolutionTracker(runs)
    snapshots = [
        s.model_dump() if hasattr(s, "model_dump") else s.dict()
        for s in tracker.snapshots
    ]
    return {"history": snapshots, "total": len(snapshots)}


@app.get(
    "/api/v1/evolution/trends", dependencies=[Depends(check_token), Depends(rate_limiter)]
)
@app.get(
    "/api/evolution/trends", dependencies=[Depends(check_token), Depends(rate_limiter)], deprecated=True
)
async def get_evolution_trends(
    limit: int = Query(default=50, ge=1, le=500),
    job_id: JobIdQuery = None,
    user: User = Depends(current_user),
) -> Any:
    """Return just the calculated trends."""
    from archguard.evolution.tracker import EvolutionTracker

    runs, repo_url = await _repo_scoped_runs(job_id, limit, user.id)
    if len(runs) < MIN_RUNS_FOR_HISTORY:
        return _insufficient_history(runs, repo_url)
    tracker = EvolutionTracker(runs)
    report = tracker.generate_report()

    def dump_trend(t: Any) -> Any:
        if not t:
            return None
        return t.model_dump() if hasattr(t, "model_dump") else t.dict()

    return {
        "health_trend": dump_trend(report.health_trend),
        "violation_trend": dump_trend(report.violation_trend),
        "debt_trend": dump_trend(report.debt_trend),
        "fitness_trend": dump_trend(report.fitness_trend),
    }


# -----------------------------------------------------------------------------
# Architecture Evolution (Git History) Endpoints
# -----------------------------------------------------------------------------

_EVO_LOCK = threading.Lock()

#: Last completed git-history report, per job.
#:
#: Was an unbounded module-level dict (D5): nothing ever evicted from it, each
#: entry holds up to 100 commit snapshots, and it was lost on restart anyway.
#: Redis gives it both a bound and durability; the in-process fallback keeps a
#: hard cap so the leak cannot come back when Redis is absent.
_EVO_TTL_SECONDS = 3600
_EVO_LOCAL_MAX = 100
_EVO_LOCAL: dict[str, Any] = {}


def _evo_key(job_id: str | None, user_id: int) -> str:
    # Keyed by user as well as job: the cache must not become a way to read a
    # report for a job you do not own but whose id you happen to have.
    return f"evolution:{user_id}:{job_id or '_no_job_id'}"


def _evo_store(job_id: str | None, user_id: int, report: dict[str, Any]) -> None:
    from archguard.redis_client import get_redis

    key = _evo_key(job_id, user_id)
    client = get_redis()
    if client is not None:
        try:
            # set(ex=) rather than setex(): redis-py deprecated the latter in 2.6.
            client.set(key, json.dumps(report, default=str), ex=_EVO_TTL_SECONDS)
            return
        except Exception as exc:
            logger.warning("Could not cache evolution report in Redis: %s", exc)
    with _EVO_LOCK:
        if len(_EVO_LOCAL) >= _EVO_LOCAL_MAX:
            _EVO_LOCAL.pop(next(iter(_EVO_LOCAL)))
        _EVO_LOCAL[key] = report


def _evo_load(job_id: str | None, user_id: int) -> dict[str, Any] | None:
    from archguard.redis_client import get_redis

    key = _evo_key(job_id, user_id)
    client = get_redis()
    if client is not None:
        try:
            # Sync client; the ``Awaitable[T] | T`` annotation comes from the
            # base class redis-py shares with its async client.
            raw = cast("bytes | str | None", client.get(key))
            if raw is not None:
                loaded: dict[str, Any] = json.loads(raw)
                return loaded
        except Exception as exc:
            logger.warning("Could not read evolution report from Redis: %s", exc)
    with _EVO_LOCK:
        return _EVO_LOCAL.get(key)


class EvolutionAnalyzeRequest(BaseModel):
    max_commits: int = Field(default=5, ge=1, le=100)


@app.post(
    "/api/v1/evolution/analyze", dependencies=[Depends(check_token), Depends(rate_limiter)]
)
@app.post(
    "/api/evolution/analyze", dependencies=[Depends(check_token), Depends(rate_limiter)], deprecated=True
)
async def start_evolution(
    body: EvolutionAnalyzeRequest,
    job_id: JobIdQuery = None,
    user: User = Depends(current_user),
) -> Any:
    """Run ArchitectureEvolutionTracker against git history."""
    from archguard.dashboard.app import get_target_path
    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.evolution.tracker import ArchitectureEvolutionTracker

    # The workspace is on disk under a path derived from the job id alone, so
    # without this check anyone holding an id could run a full history analysis
    # over someone else's clone -- and read the result.
    if job_id:
        async with session_scope() as session:
            if await store.get_job(session, job_id, user.id) is None:
                raise HTTPException(
                    status_code=404, detail=f"Job {job_id!r} not found"
                )

    try:
        target = get_target_path(job_id)
    except HTTPException:
        return {
            "error": "workspace_expired",
            "message": "Analysis workspace expired. Re-run the analysis to examine git history.",
            "snapshots": [],
            "commits_analyzed": 0,
        }

    # Detect shallow clone — git history features are meaningless with --depth=1
    shallow_marker = target / ".git" / "shallow"
    if shallow_marker.exists():
        return {
            "error": "shallow_clone",
            "message": "This repository was analyzed with a shallow clone. Git-history trends require full history.",
            "snapshots": [],
            "commits_analyzed": 0,
        }

    try:
        tracker = ArchitectureEvolutionTracker(target)
        report = tracker.analyze_history(max_commits=body.max_commits)

        # Every attempted commit failed. Returning debt_velocity 0.0 and
        # commits_analyzed 0 here looked like a successful measurement of a
        # perfectly stable repository, when in fact nothing was measured at all.
        # On the dashboard path the usual cause is concrete: the contract is
        # auto-generated into the working tree and never committed, so a
        # worktree checked out at an old commit has no .archguard.yml.
        if report.all_failed:
            reason = report.failure_summary
            logger.warning(
                "Git-history analysis measured nothing: %d/%d commits failed (%s)",
                report.failure_count, report.commits_attempted, reason,
            )
            return {
                "error": "no_commits_analyzable",
                "message": (
                    f"None of the {report.commits_attempted} commits examined could be "
                    f"analysed, so no trend could be measured. Cause: {reason}"
                ),
                "snapshots": [],
                "commits_analyzed": 0,
                "commits_attempted": report.commits_attempted,
                "commits_failed": report.failure_count,
                "failure_reason": reason,
            }

        result = {
            "snapshots": [
                {
                    "sha": s.sha,
                    "committed_at": s.committed_at,
                    "health_score": s.health_score,
                    "violation_count": s.violation_count,
                    "author": s.author,
                    "message": s.message,
                }
                for s in report.snapshots
            ],
            "debt_velocity": report.debt_velocity,
            "trend_direction": report.trend_direction,
            "score_range": {"min": report.score_range[0], "max": report.score_range[1]},
            "commits_analyzed": len(report.snapshots),
            "commits_attempted": report.commits_attempted,
            # A partial failure still yields real numbers, but they describe
            # fewer commits than the user asked for -- say so rather than
            # letting the gap pass as a complete picture.
            "commits_failed": report.failure_count,
            "failure_reason": report.failure_summary,
        }

        _evo_store(job_id, user.id, result)

        return result
    except Exception as exc:
        logger.exception("Evolution analysis failed: %s", exc)
        return {"error": "analysis_failed", "message": "Could not analyze git history.", "snapshots": [], "commits_analyzed": 0}


@app.get(
    "/api/v1/evolution/latest", dependencies=[Depends(check_token), Depends(rate_limiter)]
)
@app.get(
    "/api/evolution/latest", dependencies=[Depends(check_token), Depends(rate_limiter)], deprecated=True
)
def get_latest_evolution(
    job_id: JobIdQuery = None, user: User = Depends(current_user)
) -> Any:
    """Get the latest completed architecture evolution report."""
    cached = _evo_load(job_id, user.id)
    if cached is not None:
        return cached
    return {"available": False}
