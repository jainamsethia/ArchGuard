"""Scheduled re-scanning of watched repositories.

A watch is only worth having if something acts on it. This is that something: a
periodic pass that asks each watched repository whether its HEAD has moved and
enqueues an analysis for the ones that have.

The gate is the commit, not the file. ADR-009 measured re-analysing an unchanged
repository at ~4s in a warm worker and concluded that file-level hash gating is
not worth wiring; ``git ls-remote`` answers the same question for a few hundred
bytes and no clone at all. That difference is what decides whether watching a
hundred repositories is affordable, because the cost is multiplied by the number
of repositories rather than paid once.

Runs in the worker, not the web process. It clones and analyses, which is
exactly the work the two were split apart to keep out of the process holding
every session key.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

#: How many remotes to poll at once. Each is one short network round trip, so
#: this is a politeness limit toward GitHub rather than a resource one.
POLL_CONCURRENCY: int = int(os.environ.get("ARCHGUARD_WATCH_POLL_CONCURRENCY", "5"))

#: A ceiling on analyses started by one scheduled pass. Without it, an instance
#: whose watch list has grown -- or whose first pass finds every repository
#: unseen -- floods the queue with clones in a single tick and starves the
#: analyses users submitted interactively. The rest are picked up next pass.
MAX_ENQUEUED_PER_PASS: int = int(os.environ.get("ARCHGUARD_WATCH_MAX_PER_PASS", "20"))


def watching_enabled() -> bool:
    """Whether the scheduled pass should run at all.

    Off is a legitimate configuration: an instance can host watches for its
    dashboard without also being the one that acts on them, and an operator
    debugging a queue wants a way to stop the scheduler without stopping the
    worker.
    """
    return os.environ.get("ARCHGUARD_WATCH_ENABLED", "1").lower() not in ("0", "false", "")


async def _poll_one(entry: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Ask one remote for its HEAD. Never raises."""
    from archguard.dashboard.workspace import remote_head

    return entry, await remote_head(entry["repo_url"])


async def scan_watched_repositories(ctx: dict[str, Any] | None = None) -> int:
    """One scheduled pass. Returns how many analyses were enqueued.

    ``ctx`` is arq's per-job context, unused and defaulted so the function is
    callable directly from a test without a queue.

    Never raises. A scheduled task that dies takes every later pass with it
    until someone notices, and the thing it was watching for is exactly what
    nobody is watching.
    """
    if not watching_enabled():
        logger.info("Watch scanning is disabled; skipping this pass.")
        return 0

    from archguard.db.session import session_scope
    from archguard.db.store import all_watched
    from archguard.worker.alerts import evaluate_and_alert

    try:
        async with session_scope() as session:
            watches = await all_watched(session)
    except Exception:
        logger.exception("Could not read the watch list; skipping this pass")
        return 0

    if not watches:
        return 0

    logger.info("Scheduled pass over %d watched repositor(ies)", len(watches))

    # The remotes are polled concurrently, but no database transaction is held
    # while that happens: a transaction open across a network round trip is how
    # a slow remote turns into connection-pool exhaustion.
    semaphore = asyncio.Semaphore(POLL_CONCURRENCY)

    async def _guarded(entry: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        async with semaphore:
            return await _poll_one(entry)

    results = await asyncio.gather(
        *(_guarded(w) for w in watches), return_exceptions=True
    )

    enqueued = 0
    for result in results:
        if isinstance(result, BaseException):
            logger.warning("A watch poll failed: %s", result)
            continue
        entry, sha = result
        try:
            if await _act_on(entry, sha, enqueued >= MAX_ENQUEUED_PER_PASS):
                enqueued += 1
        except Exception:
            logger.exception(
                "Failed to act on the watch for %s; continuing", entry["repo_url"]
            )

    # Alerting is evaluated for every watch, not only the ones that changed
    # this pass. The analysis enqueued above has not finished yet -- its run
    # lands minutes later -- so a pass reports on what previous passes
    # produced. Coupling it to "did HEAD move" would mean a repository that
    # regressed and then went quiet was never reported at all.
    for entry in watches:
        try:
            await evaluate_and_alert(entry)
        except Exception:
            logger.exception(
                "Alert evaluation failed for %s; continuing", entry["repo_url"]
            )

    if enqueued:
        logger.info("Scheduled pass enqueued %d analysis(es)", enqueued)
    return enqueued


async def _act_on(entry: dict[str, Any], sha: str | None, at_capacity: bool) -> bool:
    """Record the check and, when HEAD has moved, enqueue. Returns whether it did."""
    from archguard.db.session import session_scope
    from archguard.db.store import create_job, record_watch_check
    from archguard.worker.queue import enqueue_analysis

    unchanged = sha is not None and sha == entry["last_seen_sha"]

    if sha is None or unchanged:
        # Both the unreachable case and the nothing-changed case write the
        # timestamp. A watcher that has quietly stopped working is otherwise
        # indistinguishable from a repository nobody has touched.
        async with session_scope() as session:
            await record_watch_check(
                session, entry["user_id"], entry["repository_id"], sha
            )
        return False

    if at_capacity:
        # Deliberately does not record the sha: leaving it unchanged is what
        # makes the next pass pick this repository up instead of concluding it
        # has already been handled.
        logger.info(
            "Reached the per-pass enqueue limit; %s will be picked up next pass",
            entry["repo_url"],
        )
        return False

    async with session_scope() as session:
        job = await create_job(session, entry["repo_url"], user_id=entry["user_id"])
        job_id = job.id
        # Recorded now rather than when the analysis finishes. An analysis that
        # fails would otherwise be retried on every pass forever, re-cloning a
        # repository that has already been shown not to work; this way a failure
        # waits for the next real change, and the failed run is visible on the
        # dashboard either way.
        await record_watch_check(
            session, entry["user_id"], entry["repository_id"], sha
        )

    await enqueue_analysis(job_id)
    logger.info(
        "%s moved to %s; enqueued analysis %s", entry["repo_url"], sha[:8], job_id
    )
    return True
