"""The analysis worker process.

Run with::

    arq archguard.worker.main.WorkerSettings

Separate from the web process for four reasons, in order of how badly each one
bit: a restart used to cancel every running analysis; a second web instance
could not see the first one's jobs; the web image carried torch and
sentence-transformers for work the web process never does; and untrusted
repositories were parsed in the process holding every session key.
"""

from __future__ import annotations

import logging
import os
from typing import Any, ClassVar

from arq import cron

from archguard.observability.logger import configure_logging
from archguard.worker.settings import redis_settings
from archguard.worker.tasks import analyse_repository
from archguard.worker.watch import scan_watched_repositories

logger = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging()

    # The worker needs this more than the web process does: it has no HTTP
    # surface, so a crash there is invisible unless something ships it out.
    from archguard.observability.errors import configure_error_reporting

    configure_error_reporting()
    logger.info("Analysis worker starting up")


async def shutdown(ctx: dict[str, Any]) -> None:
    from archguard.db.session import dispose_engine
    from archguard.redis_client import close_redis

    await dispose_engine()
    close_redis()
    logger.info("Analysis worker shut down")


def _watch_minutes() -> set[int]:
    """Which minutes past the hour the scheduled pass runs on.

    Expressed as an interval in minutes rather than a cron expression, because
    the only thing an operator wants to change here is how often it runs, and
    a cron string invites getting the other four fields wrong. Values that do
    not divide 60 are rounded up to one that does, so the gap between passes
    stays even -- an interval of 25 would otherwise fire at :00 and :25 and
    then wait 35 minutes.
    """
    requested = int(os.environ.get("ARCHGUARD_WATCH_INTERVAL_MINUTES", "60"))
    if requested >= 60:
        return {0}
    for step in (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30):
        if step >= max(requested, 1):
            return set(range(0, 60, step))
    return {0}


class WorkerSettings:
    """arq entry point."""

    functions: ClassVar[list[Any]] = [analyse_repository]
    on_startup = startup
    on_shutdown = shutdown

    #: The scheduled pass over watched repositories.
    #:
    #: Safe to run several workers. arq's ``cron(unique=True)`` -- the default
    #: -- gives each scheduled run the job id ``"<name>:<next_run_ms>"``, so
    #: every worker enqueues the same id for a given tick and all but one are
    #: dropped as duplicates. The pass therefore happens once per tick, not
    #: once per worker.
    #:
    #: ``run_at_startup`` is off deliberately. A rolling deploy restarts every
    #: worker, and a pass on each start would poll every watched repository
    #: several times over in the space of a minute.
    cron_jobs: ClassVar[list[Any]] = [
        cron(
            scan_watched_repositories,
            minute=_watch_minutes(),
            run_at_startup=False,
            timeout=int(os.environ.get("ARCHGUARD_WATCH_PASS_TIMEOUT", "600")),
        )
    ]

    #: A value, not a method. arq reads this attribute directly, so a
    #: ``@staticmethod`` here was handed to it verbatim and every worker start
    #: died with "'staticmethod' object has no attribute 'host'". Evaluated at
    #: import, which is why the enqueue side imports ``settings`` instead of
    #: this module: the web process must not require REDIS_URL to start.
    redis_settings = redis_settings()

    #: How many analyses one worker runs at once. Each holds a full clone on
    #: disk and, with layers 3 and 4 enabled, a loaded embedding model in
    #: memory -- so this is a memory budget, not a throughput knob. Scale out
    #: with more worker processes rather than up with this number.
    max_jobs = int(os.environ.get("ARCHGUARD_WORKER_CONCURRENCY", "2"))

    #: An analysis is capped at 600s inside the pipeline; this is the outer
    #: bound including the clone.
    job_timeout = int(os.environ.get("ARCHGUARD_JOB_TIMEOUT", "900"))

    #: One attempt. `analyse_repository` catches its own exceptions and records
    #: a failed job, so a retry would only re-clone a repository that has
    #: already been shown to fail -- three times, by default, for nothing.
    max_tries = 1

    #: Long enough to read a result off the queue after the fact; the durable
    #: record is the run in PostgreSQL, not this.
    keep_result = 3600
