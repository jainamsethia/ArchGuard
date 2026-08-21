"""Enqueueing work, and what happens when there is no queue.

There is exactly one branch in this file, and it is the only one in the
codebase: submit to arq when a queue is configured, run in-process when one is
not. Everything downstream of that branch is the same function
(``tasks.analyse_repository``), so local development exercises the code
production runs rather than a simplified stand-in.

The in-process path exists so ``make dev`` is one command instead of two. It is
not a supported deployment: it cannot survive a restart and cannot be shared
between replicas, which is why the production config check refuses to start
without ``REDIS_URL``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

QUEUE_NAME = "archguard:analysis"

#: Tasks started in-process, kept referenced so the event loop does not garbage
#: collect a running coroutine out from under itself.
_INLINE_TASKS: set[asyncio.Task[Any]] = set()


def queue_available() -> bool:
    """Whether work can be handed to a worker rather than run here."""
    if os.environ.get("ARCHGUARD_INLINE_ANALYSIS", "").lower() in ("1", "true"):
        # An explicit override, for tests and for debugging an analysis in the
        # web process with a debugger attached.
        return False
    from archguard.redis_client import is_configured

    return is_configured()


async def enqueue_analysis(job_id: str) -> str:
    """Hand a job to the worker pool, or run it here. Returns how.

    Returning which path was taken rather than logging it: the caller puts it in
    the API response, so an operator can tell from a single request whether the
    instance they are looking at has a worker behind it.
    """
    if queue_available():
        try:
            await _enqueue(job_id)
            return "queued"
        except Exception:
            # A queue that is configured but unreachable must not silently
            # become an in-process analysis: that is how a web dyno ends up
            # quietly running the workload it was split apart to avoid. Fail
            # the submission instead, and let the caller report it.
            logger.exception("[job %s] Could not enqueue; refusing to run inline", job_id)
            raise

    logger.info(
        "[job %s] No queue configured; running the analysis in this process. "
        "This is a development convenience -- it is lost on restart and not "
        "shared between instances.",
        job_id,
    )
    _run_inline(job_id)
    return "inline"


async def _enqueue(job_id: str) -> None:
    from arq import create_pool

    from archguard.worker.settings import redis_settings

    pool = await create_pool(redis_settings())
    try:
        # _job_id makes the enqueue idempotent: a retried submission of the
        # same analysis is dropped by arq rather than cloning the repository
        # twice.
        await pool.enqueue_job(
            "analyse_repository", job_id, _job_id=f"{QUEUE_NAME}:{job_id}"
        )
    finally:
        await pool.close()


def _run_inline(job_id: str) -> None:
    from archguard.worker.tasks import analyse_repository

    task = asyncio.create_task(analyse_repository(None, job_id))
    _INLINE_TASKS.add(task)
    task.add_done_callback(_INLINE_TASKS.discard)


async def cancel_inline_tasks(timeout: float = 5.0) -> int:
    """Cancel in-process analyses on shutdown. Returns how many were running.

    Only reachable on the development path -- a real worker's jobs stay on the
    queue when the web process stops, which is the entire point.
    """
    pending = list(_INLINE_TASKS)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending, timeout=timeout)
    return len(pending)
