"""Liveness, readiness, and metrics.

``/health`` used to be the only one, and it returned 200 whenever the process
was alive. A platform health check pointed at it therefore reported a service
as healthy while its database was unreachable, its Redis was down and every
request was failing -- which is worse than no health check, because it stops
the platform from restarting or rolling back.

The split is the standard one and the distinction matters:

* ``/health`` -- liveness. Is this process running? Always 200. A failing
  liveness check gets the container killed, so it must not depend on anything
  outside the process: a database blip would otherwise trigger a restart
  storm, taking down the instances that were still serving.
* ``/ready`` -- readiness. Can this process serve a request? 503 when it
  cannot, so the load balancer stops sending traffic while it recovers.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Response

logger = logging.getLogger(__name__)

router = APIRouter()

#: A dependency check that takes longer than this is treated as failed. A
#: readiness probe that hangs is the same as one that fails, except the
#: platform waits for its own timeout to find out.
CHECK_TIMEOUT_SECONDS = 3.0


@router.get("/health", summary="Liveness check", tags=["meta"])
async def health_check() -> dict[str, Any]:
    """Is the process alive? Always 200.

    Deliberately checks nothing external. This answer decides whether the
    container gets killed, and killing a healthy process because Postgres was
    briefly unreachable turns one outage into two.
    """
    from archguard.dashboard.app import _APP_START_TIME, _installed_version

    return {
        "status": "ok",
        "version": _installed_version(),
        "environment": os.environ.get("ENVIRONMENT", "development"),
        "uptime_seconds": round(time.time() - _APP_START_TIME),
    }


async def _check_database() -> tuple[bool, str]:
    try:
        from sqlalchemy import text

        from archguard.db.session import session_scope

        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:
        # The class name, not the message: a connection error's message
        # contains the URL, and the URL contains the password.
        return False, type(exc).__name__


async def _check_redis() -> tuple[bool, str]:
    try:
        from archguard.redis_client import is_configured, ping

        if not is_configured():
            return False, "REDIS_URL is not set"
        return (True, "ok") if ping() else (False, "PING failed")
    except Exception as exc:
        return False, type(exc).__name__


async def _check_git() -> tuple[bool, str]:
    """git is how repositories arrive. Without it nothing can be analysed."""
    git = shutil.which("git")
    if not git:
        return False, "git is not on PATH"

    def _run() -> None:
        subprocess.run(
            [git, "--version"],
            capture_output=True,
            timeout=CHECK_TIMEOUT_SECONDS,
            check=True,
        )

    try:
        # On a thread: subprocess.run blocks, and a readiness probe that stalls
        # the event loop makes every concurrent request wait on it -- which is
        # the opposite of what a probe is for.
        await asyncio.to_thread(_run)
        return True, "ok"
    except Exception as exc:
        return False, type(exc).__name__


async def _check_data_dir() -> tuple[bool, str]:
    """Probe-write, because the audit logger swallows write failures by design.

    A root-owned persistent disk under a container running as uid 1000 fails
    every write silently and forever (E8), so the only way to know is to try.
    """
    directory = Path(os.environ.get("ARCHGUARD_DATA_DIR", ".archguard-cache"))
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".probe-", delete=True):
            pass
        return True, "ok"
    except OSError as exc:
        return False, type(exc).__name__


CHECKS = {
    "database": _check_database,
    "redis": _check_redis,
    "git": _check_git,
    "data_dir": _check_data_dir,
}


@router.get("/ready", summary="Readiness check", tags=["meta"])
async def readiness_check(response: Response) -> dict[str, Any]:
    """Can this process serve a request? 503 when it cannot.

    Every check runs even after one fails. An operator looking at this wants
    the whole picture, and stopping at the first failure means finding out
    about the second one after fixing the first.
    """
    results: dict[str, Any] = {}
    ready = True
    for name, check in CHECKS.items():
        try:
            ok, detail = await check()
        except Exception as exc:
            ok, detail = False, type(exc).__name__
        results[name] = {"ok": ok, "detail": detail}
        ready = ready and ok

    if not ready:
        response.status_code = 503
        logger.warning(
            "Readiness check failed: %s",
            {k: v["detail"] for k, v in results.items() if not v["ok"]},
        )

    return {"ready": ready, "checks": results}


@router.get("/metrics", summary="Prometheus metrics", tags=["meta"])
async def metrics() -> Response:
    """Prometheus text format.

    Written by hand rather than with a client library: the numbers all come
    from one database query and a Redis length, and a dependency that mostly
    provides a registry and a decorator is not worth an import in the web
    image. If this grows a histogram with real buckets, revisit.

    Unauthenticated, like /health and /ready. It exposes counts, not content --
    no repository URLs, no module names, no findings. Restrict it at the proxy
    if the deployment needs to.
    """
    from archguard.dashboard.app import _APP_START_TIME, _installed_version

    lines: list[str] = []

    def emit(name: str, value: Any, help_text: str, kind: str = "gauge",
             labels: str = "") -> None:
        if not any(line.startswith(f"# HELP {name} ") for line in lines):
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")
        lines.append(f"{name}{labels} {value}")

    emit(
        "archguard_up",
        1,
        "1 when the web process is serving.",
    )
    emit(
        "archguard_uptime_seconds",
        round(time.time() - _APP_START_TIME),
        "Seconds since this process started.",
    )
    emit(
        "archguard_build_info",
        1,
        "Version, as a label on a constant.",
        labels=f'{{version="{_installed_version()}"}}',
    )

    # Job counts by status. The one number that says whether the worker is
    # keeping up: queued climbing while complete does not means it is not.
    try:
        from sqlalchemy import func, select

        from archguard.db.models import Job, Run, User
        from archguard.db.session import session_scope

        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(Job.status, func.count()).group_by(Job.status)
                )
            ).all()
            for status, count in rows:
                emit(
                    "archguard_jobs_total",
                    count,
                    "Analysis jobs, by status.",
                    kind="gauge",
                    labels=f'{{status="{status}"}}',
                )
            if not rows:
                emit("archguard_jobs_total", 0, "Analysis jobs, by status.",
                     labels='{status="none"}')

            emit(
                "archguard_runs_total",
                (await session.execute(select(func.count()).select_from(Run))).scalar_one(),
                "Completed analysis runs stored.",
                kind="counter",
            )
            emit(
                "archguard_users_total",
                (await session.execute(select(func.count()).select_from(User))).scalar_one(),
                "Registered accounts.",
            )
        emit("archguard_database_up", 1, "1 when the database answered.")
    except Exception as exc:
        # A metrics endpoint that 500s when the database is down is a metrics
        # endpoint that goes quiet exactly when it is needed.
        logger.warning("Metrics: database query failed: %s", exc)
        emit("archguard_database_up", 0, "1 when the database answered.")

    try:
        from archguard.redis_client import get_redis

        client = get_redis()
        if client is None:
            emit("archguard_redis_up", 0, "1 when Redis answered.")
        else:
            client.ping()
            emit("archguard_redis_up", 1, "1 when Redis answered.")
            # arq's pending set. Queue depth is the number an operator pages on.
            try:
                depth = client.zcard("arq:queue")
            except Exception:
                depth = 0
            emit(
                "archguard_queue_depth",
                depth,
                "Analyses waiting for a worker.",
            )
    except Exception as exc:
        logger.warning("Metrics: redis query failed: %s", exc)
        emit("archguard_redis_up", 0, "1 when Redis answered.")

    # What the AI features have cost. The endpoint reports usage on every call
    # and the client used to discard it, so the only way to answer "how much is
    # this spending" was the provider's billing page. Counters rather than
    # gauges: a total that only goes up survives a missed scrape, and Prometheus
    # derives the rate.
    try:
        from archguard.llm.usage import totals

        usage = totals()
        emit(
            "archguard_llm_calls_total",
            usage["calls"],
            "LLM API calls made.",
            kind="counter",
        )
        for field, help_text in (
            ("prompt_tokens", "Prompt tokens sent to the LLM."),
            ("completion_tokens", "Completion tokens returned by the LLM."),
            (
                "total_tokens",
                # Deliberately not described as prompt plus completion, because
                # it is not. Gemini 3.x are thinking models and reasoning tokens
                # are billed in the total without appearing in either of the
                # other two: measured on a one-word answer, 74 + 1 against a
                # total of 285. An operator comparing the three and finding they
                # do not add up would reasonably conclude the metric is broken.
                "Total LLM tokens billed, including reasoning tokens that appear "
                "in neither the prompt nor the completion count.",
            ),
        ):
            emit(
                f"archguard_llm_{field}_total", usage[field], help_text, kind="counter"
            )
    except Exception as exc:
        # Counts, never content: no prompts, no questions, no repository names.
        logger.debug("Metrics: llm usage unavailable: %s", exc)

    body = "\n".join(lines) + "\n"
    return Response(content=body, media_type="text/plain; version=0.0.4")
