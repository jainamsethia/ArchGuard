"""The daily sweep that keeps watched repositories current.

An arq cron job rather than a second scheduler. arq already runs, already
holds the queue, and already survives a restart; adding APScheduler or Celery
beat beside it would mean two things that can be down independently and two
places to look when a scan does not happen.

The sweep enqueues ordinary `analyse_repository` jobs. It does not analyse
anything itself -- a watched repository's scan is the same scan a person gets
by pasting the URL, which is what keeps the two from drifting apart. It also
means the cron tick is cheap: a query and some enqueues, not an hour of work
holding the worker.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

#: How stale a watch has to be before it is rescanned. Slightly under a day so
#: a sweep that runs a few minutes late does not skip the day entirely and push
#: every scan 24 hours out.
DUE_AFTER = timedelta(hours=23)


async def sweep_watched(ctx: dict[str, Any] | None = None) -> int:
    """Enqueue a rescan for every watched repository that is due.

    Returns how many were enqueued, which is what the worker logs and what the
    tests assert on.

    Never raises: a cron job that throws stops the sweep partway through, and
    the repositories after the failure would wait another day.
    """
    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.worker.queue import enqueue_analysis

    try:
        async with session_scope() as session:
            due = await store.watches_due(session, datetime.now(UTC) - DUE_AFTER)
    except Exception:
        logger.exception("Watched-repository sweep could not read the schedule")
        return 0

    if not due:
        return 0

    enqueued = 0
    for watch in due:
        try:
            async with session_scope() as session:
                # A real job owned by the watcher, so the run it produces is
                # theirs: visible in their history, scoped by their user_id,
                # and comparable against their previous run rather than
                # anybody else's.
                job = await store.create_job(
                    session, watch["repo_url"], user_id=watch["user_id"]
                )
                job_id = job.id
            await enqueue_analysis(job_id)
            enqueued += 1
        except Exception:
            # One repository that cannot be enqueued must not cost the others
            # their scan.
            logger.exception(
                "Could not enqueue the scheduled scan for watch %s", watch["id"]
            )

    logger.info("Watched-repository sweep enqueued %d scan(s)", enqueued)
    return enqueued
