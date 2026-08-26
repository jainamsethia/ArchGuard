"""Deciding whether a rescan found something worth telling someone about.

Pure: two persisted runs and a threshold in, a regression or None out. Nothing
here touches the database, the network, or the clock, which is what makes the
threshold behaviour testable rather than tuned by feel.

The bar for alerting is deliberately higher than the bar for reporting. A
dashboard can show every finding; a notification that fires on noise gets muted,
and a muted notification is worth less than none at all -- it costs the same and
catches nothing. So three things qualify, all of them changes rather than
states:

* health fell by more than this watch's threshold;
* a fitness gate that was passing is now failing;
* a critical or high-severity violation appeared that was not there before.

Health is judged through ``alerting.trend_detector`` rather than by comparing
numbers here, so there is one definition of "degrading" in the codebase. That
matters because it once had the sign backwards -- a repository whose health rose
from 50 to 90 was alerted on as "degrading by 40" (C10, fixed in 0d77e38) -- and
a second copy of the comparison is a second place for that to happen.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

#: Severities worth waking someone for. A new low-severity finding belongs in
#: the dashboard, not in a notification.
ALERTING_SEVERITIES = frozenset({"critical", "high"})


@dataclass(frozen=True)
class Regression:
    """What changed for the worse, and how to recognise it again."""

    kind: str
    summary: str
    alert_key: str


def _violation_identity(violation: dict[str, Any]) -> tuple[str, str, str]:
    """What makes a violation the same violation across two runs.

    Not the line number: a finding that shifted because something above it grew
    is the same finding, and treating it as new would alert on every edit to an
    unrelated part of the file.
    """
    return (
        str(violation.get("module") or ""),
        str(violation.get("layer") or ""),
        str(violation.get("message") or ""),
    )


def _failing_gates(run: dict[str, Any]) -> set[str]:
    results = (run.get("metrics") or {}).get("fitness_results") or []
    return {
        str(r.get("name") or "")
        for r in results
        if isinstance(r, dict) and not r.get("passed", True)
    }


def _alert_key(run_id: Any, kind: str, detail: str) -> str:
    """A deterministic identity for one regression event.

    Derived from the run and the regression rather than generated, so a worker
    that sends an alert and dies before recording it computes the same key on
    the next attempt and skips the duplicate. In-memory flags do not survive the
    restart that causes the problem in the first place.
    """
    raw = f"{run_id}|{kind}|{detail}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def detect(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    threshold: float,
) -> Regression | None:
    """The first regression found between two runs, or None.

    First rather than all: an alert is a nudge to go and look, and one reason to
    look is enough. The dashboard has the full picture.

    A repository with no previous run never regresses -- there is nothing to
    have regressed from, and "your repository has problems" on a first scan is
    the dashboard's job.
    """
    if previous is None:
        return None

    run_id = current.get("id")

    # 1. Health, through the shared trend detector.
    drop = _health_regression(previous, current, threshold)
    if drop is not None:
        return Regression(
            kind="health_drop",
            summary=drop,
            alert_key=_alert_key(run_id, "health_drop", drop),
        )

    # 2. A gate that was holding and is not any more.
    newly_failing = sorted(_failing_gates(current) - _failing_gates(previous))
    if newly_failing:
        summary = f"Fitness gate now failing: {', '.join(newly_failing)}."
        return Regression(
            kind="fitness_gate",
            summary=summary,
            alert_key=_alert_key(run_id, "fitness_gate", ",".join(newly_failing)),
        )

    # 3. A severe finding that was not there last time.
    before = {_violation_identity(v) for v in previous.get("violations") or []}
    new_severe = [
        v
        for v in current.get("violations") or []
        if str(v.get("severity") or "").lower() in ALERTING_SEVERITIES
        and _violation_identity(v) not in before
    ]
    if new_severe:
        first = new_severe[0]
        summary = (
            f"{len(new_severe)} new {'issue' if len(new_severe) == 1 else 'issues'} "
            f"at critical or high severity, including {first.get('module') or 'unknown'}: "
            f"{first.get('message') or ''}"
        ).strip()
        detail = ";".join(sorted("|".join(_violation_identity(v)) for v in new_severe))
        return Regression(
            kind="new_violation",
            summary=summary,
            alert_key=_alert_key(run_id, "new_violation", detail),
        )

    return None


def _health_regression(
    previous: dict[str, Any], current: dict[str, Any], threshold: float
) -> str | None:
    """A health drop worth reporting, described, or None.

    `detect_trends` refuses to say anything until it has `window` runs -- ten by
    default -- which for a watched repository would mean no alert until its
    tenth scan. Narrowed to the pair actually being compared, which is what the
    window is for here: this is a before-and-after, not a smoothed trend.
    """
    from archguard.alerting.trend_detector import detect_trends

    alerts = detect_trends(
        [previous, current], window=2, degradation_threshold=threshold
    )
    for alert in alerts:
        if alert.direction == "degrading":
            before = previous.get("score")
            after = current.get("score")
            return f"Health fell from {before} to {after}."
    return None
