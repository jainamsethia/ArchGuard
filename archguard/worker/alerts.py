"""Regression alerts for watched repositories.

``archguard.alerting`` has had the two halves of this for a while -- trend
detection over recorded runs, and SSRF-guarded webhook delivery -- with no
caller. This is the caller: after the scheduled pass has re-scanned what
changed, it looks at the runs a repository has accumulated and reports the ones
that got worse.

Only degradations are delivered. The detector reports improvements too, and
they are worth having on the dashboard, but an alert about good news is noise
in a channel people are supposed to react to.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        logger.warning("%s is not an integer; using %s", name, default)
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        logger.warning("%s is not a number; using %s", name, default)
        return default


def alert_window() -> int:
    """How many runs a trend is measured over.

    Also the number of analyses a repository needs before any alert is
    possible, which is the part worth knowing: at the detector's default of 10,
    a repository watched and scanned daily says nothing for its first ten days.
    Lowered here so a watch becomes useful within a few scans, and configurable
    because the right answer depends on how often a repository actually changes.
    """
    from archguard.alerting.trend_detector import DEFAULT_WINDOW

    return max(2, _int_env("ARCHGUARD_ALERT_WINDOW", min(5, DEFAULT_WINDOW)))


def alert_threshold() -> float:
    """How many health points a drop must be before it is worth sending."""
    from archguard.alerting.trend_detector import DEFAULT_DEGRADATION_THRESHOLD

    return _float_env("ARCHGUARD_ALERT_THRESHOLD", DEFAULT_DEGRADATION_THRESHOLD)


def webhook_targets() -> list[tuple[str, str]]:
    """Configured destinations as ``(kind, url)``.

    Instance-level rather than per-user. A per-user destination means storing a
    URL somebody else supplied and then having a scheduled job POST to it,
    which is a different security problem than the one the SSRF guard in
    ``archguard.alerting.webhooks`` was written for, and not one to take on
    quietly as part of wiring up a scheduler.
    """
    targets = []
    slack = os.environ.get("ARCHGUARD_SLACK_WEBHOOK", "").strip()
    if slack:
        targets.append(("slack", slack))
    generic = os.environ.get("ARCHGUARD_ALERT_WEBHOOK", "").strip()
    if generic:
        targets.append(("generic", generic))
    return targets


async def deliver(alerts: list[Any]) -> int:
    """Send *alerts* to every configured destination. Returns how many sent.

    Never raises. Delivery failures are already reported by
    ``webhooks._check_response`` -- a rejected webhook must not take down the
    scheduled pass that produced it.
    """
    from archguard.alerting.webhooks import send_generic_webhook, send_slack_alert

    sent = 0
    for kind, url in webhook_targets():
        try:
            if kind == "slack":
                await send_slack_alert(url, alerts)
            else:
                await send_generic_webhook(url, alerts)
            sent += 1
        except Exception:
            logger.exception("Could not deliver %d alert(s) to the %s webhook", len(alerts), kind)
    return sent


async def evaluate_and_alert(entry: dict[str, Any]) -> int:
    """Look for a regression in one watched repository. Returns alerts sent.

    ``entry`` is a row from ``store.all_watched``.
    """
    from archguard.alerting.trend_detector import detect_trends
    from archguard.db.session import session_scope
    from archguard.db.store import get_runs_for_repository, record_watch_alert

    if not webhook_targets():
        # Nothing to deliver to. Detecting a trend nobody receives is work for
        # its own sake, and doing it would still burn the dedup marker.
        return 0

    window = alert_window()

    async with session_scope() as session:
        runs = await get_runs_for_repository(
            session, entry["repo_url"], entry["user_id"], limit=window
        )

    if len(runs) < window:
        return 0

    # get_runs_for_repository returns newest first; detect_trends reads
    # runs[0] as the oldest in the window and runs[-1] as the newest. Handing
    # it the list unreversed inverts every delta -- which is C10 exactly, the
    # bug this module was already bitten by once, and it would report a
    # recovering repository as degrading.
    chronological = list(reversed(runs))

    alerts = detect_trends(
        chronological, window=window, degradation_threshold=alert_threshold()
    )
    regressions = [a for a in alerts if a.direction == "degrading"]
    if not regressions:
        return 0

    newest_sha = chronological[-1].get("commit_sha") or ""
    if newest_sha and newest_sha == entry.get("last_alerted_sha"):
        # The same regression is still inside the window and will be until it
        # ages out. Reporting it every pass is how an alert becomes a filter
        # rule.
        return 0

    sent = await deliver(regressions)
    if sent:
        async with session_scope() as session:
            await record_watch_alert(
                session, entry["user_id"], entry["repository_id"], newest_sha
            )
        logger.info(
            "Reported %d regression(s) for %s to %d destination(s)",
            len(regressions), entry["repo_url"], sent,
        )
    return len(regressions) if sent else 0
