"""What happens to a watched repository after one of its scans finishes.

Runs at the end of the ordinary analysis task rather than in a pipeline of its
own: a scheduled rescan is an ordinary job, and the only thing that makes it
special is that somebody asked to be told about the result. Building a second
analysis path for watched repositories would mean two things to keep correct.

The order matters and is not arbitrary:

    compare -> already alerted? -> send -> record

Recording last. A worker that dies mid-send has not recorded the key, so the
next attempt sends it -- one duplicate alert is a nuisance. Recording first
would mean a worker that dies before sending never sends at all, and a missed
regression is the failure this feature exists to prevent.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


async def evaluate_after_run(job_id: str) -> None:
    """Compare a finished run against the previous one and alert if it regressed.

    Never raises. An analysis that succeeded must not be reported as failed
    because a webhook was unreachable -- the run is the product, the alert is a
    courtesy on top of it.
    """
    try:
        await _evaluate(job_id)
    except Exception:
        logger.exception("[job %s] Watched-repository evaluation failed", job_id)


async def _evaluate(job_id: str) -> None:
    from archguard.db import store
    from archguard.db.models import Job
    from archguard.db.session import session_scope
    from archguard.watch import regression

    async with session_scope() as session:
        job = await session.get(Job, job_id)
        if job is None or job.user_id is None or job.repository_id is None:
            return
        watch = await store.find_watch_for_run(session, job.user_id, job.repository_id)
        if watch is None:
            return

        watch_id = watch.id
        threshold = watch.health_drop_threshold
        webhook_url = watch.webhook_url
        already_alerted = watch.last_alert_key

        current = await store.get_latest_run(session, job_id, job.user_id)
        previous = await store.get_previous_run_for_job(session, job_id)

    if current is None:
        return

    found = regression.detect(previous, current, threshold=threshold)

    if found is None:
        await _record(watch_id, status=_healthy_status(current), alert_key=None)
        return

    # The duplicate guard. Derived from the run and the regression, so a retry
    # of the same work computes the same key and stops here rather than sending
    # a second copy of an alert the user already has.
    if already_alerted == found.alert_key:
        logger.info(
            "[watch %s] Regression already alerted (%s); not sending again",
            watch_id,
            found.kind,
        )
        await _record(watch_id, status=found.summary, alert_key=None)
        return

    delivered = await _deliver(watch_id, webhook_url, found, current)
    await _record(
        watch_id,
        status=found.summary,
        alert_key=found.alert_key if delivered else None,
    )


def _healthy_status(run: dict[str, Any]) -> str:
    score = run.get("score")
    return f"No regression. Health {score}." if score is not None else "No regression."


async def _deliver(
    watch_id: int, webhook_url: str | None, found: Any, run: dict[str, Any]
) -> bool:
    """Send the alert. True when it went out and should not be sent again.

    A watch with no webhook is not a failure: the regression is recorded and
    shown in the dashboard, which is where most people will look. Returning
    True keeps it from being re-evaluated as new on the next scan.
    """
    if not webhook_url:
        return True

    from archguard.alerting.trend_detector import TrendAlert
    from archguard.alerting.webhooks import send_generic_webhook

    alert = TrendAlert(
        metric=found.kind,
        module=run.get("project_name") or run.get("repo_url") or "repository",
        direction="degrading",
        delta=0.0,
        window=2,
        message=found.summary,
    )

    try:
        # send_generic_webhook validates the destination against the SSRF guard
        # on every send -- not only when it was configured -- because DNS can
        # be repointed at an internal address after the fact, and connects to
        # the address that check approved rather than resolving the name again.
        # It also carries a timeout, refuses redirects, and checks the status.
        await send_generic_webhook(webhook_url, [alert])
        return True
    except Exception:
        logger.exception("[watch %s] Could not deliver alert", watch_id)
        return False


async def _record(watch_id: int, *, status: str, alert_key: str | None) -> None:
    """Write back what this evaluation concluded.

    `last_checked_at` always, so the scheduler moves on. `last_alert_key` only
    when something was actually delivered -- recording it after a failed send
    would suppress the retry that should follow.
    """
    from archguard.db.models import WatchedRepository
    from archguard.db.session import session_scope

    async with session_scope() as session:
        watch = await session.get(WatchedRepository, watch_id)
        if watch is None:
            return
        now = datetime.now(UTC)
        watch.last_checked_at = now
        watch.last_status = status
        if alert_key is not None:
            watch.last_alert_key = alert_key
            watch.last_alert_at = now
