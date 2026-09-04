"""Expiring analysis history, on the worker that is already scheduled.

Nothing expired before this. Runs, findings, jobs, dependency scans and file
hashes accumulated from the first analysis an account ever ran until the
account was deleted -- so the privacy policy could describe what is stored but
not for how long, because the answer was "forever".

The policy is deliberately small, and what it leaves alone is the argument:

* A completed run and everything hanging off it is kept for ARCHGUARD_RETENTION_DAYS.
  Long enough for the trend chart and Compare Runs to be worth having, bounded
  enough that a busy instance does not grow without limit.
* An unfinished job is never touched, at any age. A job stuck in `analysing`
  since a worker died is the row an operator needs in order to find out why.
* File hashes go only when their repository has no runs left. They are the
  incremental cache; dropping them early costs the next scan its reuse.
* Watched repositories and suppressions never expire. They are configuration,
  not history, and a watch that vanished because nothing regressed for three
  months would stop the monitoring at the moment it had been quietly working.

It runs as a cron on the arq worker that already sweeps watched repositories.
arq runs `cron_jobs` on exactly one worker even when several are up, which is
what stops three of them racing on the same DELETE -- and it is why this is a
schedule entry rather than a second scheduler.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

#: How long a completed run is kept. Ninety days is roughly a quarter, which is
#: the span over which an architectural trend is worth reading; shorter makes
#: the evolution view thin, and longer stores data nobody looks at.
RETENTION_DAYS = int(os.environ.get("ARCHGUARD_RETENTION_DAYS", "90"))

#: Rows per pass. The sweep is bounded so one transaction cannot try to delete
#: a year of history at once and hold locks for the length of it; a backlog
#: drains over successive nights rather than in one stall.
BATCH = int(os.environ.get("ARCHGUARD_RETENTION_BATCH", "500"))


async def purge_expired_data(ctx: dict[str, Any] | None = None) -> int:
    """Delete history past the retention window. Returns how many rows went.

    Signature and return type mirror `sweep_watched`, which is what arq's
    `WorkerCoroutine` accepts; the per-table breakdown goes to the log and is
    available from `purge_expired_runs` for anything that needs it.

    Never raises. A cron that throws takes the worker's scheduler with it, and
    the next night's pass would find the same rows still expired anyway.
    """
    del ctx
    from archguard.db.session import session_scope
    from archguard.db.store import purge_expired_runs

    try:
        async with session_scope() as session:
            removed = await purge_expired_runs(
                session, retain_days=RETENTION_DAYS, limit=BATCH
            )
    except Exception:
        logger.exception("Retention sweep failed; nothing was deleted this pass")
        return 0

    if any(removed.values()):
        logger.info(
            "Retention sweep removed %d run(s), %d job(s), %d file-hash row(s) "
            "older than %d days",
            removed["runs"],
            removed["jobs"],
            removed["file_hashes"],
            RETENTION_DAYS,
        )
    else:
        logger.info("Retention sweep: nothing older than %d days", RETENTION_DAYS)
    return sum(removed.values())
