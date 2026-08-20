"""Trend detection over recorded analysis runs (archguard.alerting).

This package is preserved through the CLI removal because watched repositories
depend on it. It had no dedicated tests -- 7.1% branch coverage, the weakest
package in the repository -- which is how C10 survived: every recorded ``score``
is a *health* score where higher is better, but the direction was derived as if
higher were worse, so an improving repository was alerted on as degrading.
"""

from __future__ import annotations

from typing import Any

import pytest

from archguard.alerting.trend_detector import TrendAlert, detect_trends


def _runs(scores: list[float], modules: list[dict[str, float]] | None = None) -> list[dict[str, Any]]:
    mods = modules or [{} for _ in scores]
    return [{"score": s, "module_scores": m} for s, m in zip(scores, mods, strict=True)]


# ---------------------------------------------------------------------------
# C10: direction
# ---------------------------------------------------------------------------


def test_rising_health_score_is_reported_as_improving() -> None:
    """The C10 regression.

    ``score`` is the health score (0-100, higher is better), so a positive
    delta is an improvement. Reporting it as "degrading" would page a team
    about their own progress and, worse, train them to ignore the alert.
    """
    alerts = detect_trends(_runs([50.0] * 9 + [90.0]), window=10, degradation_threshold=5.0)

    overall = [a for a in alerts if a.metric == "health_score"]
    assert overall, "a 40-point swing should raise an alert"
    assert overall[0].direction == "improving"
    assert "improving" in overall[0].message


def test_falling_health_score_is_reported_as_degrading() -> None:
    alerts = detect_trends(_runs([90.0] * 9 + [50.0]), window=10, degradation_threshold=5.0)

    overall = [a for a in alerts if a.metric == "health_score"]
    assert overall
    assert overall[0].direction == "degrading"
    assert overall[0].delta == pytest.approx(40.0)


def test_direction_is_consistent_between_overall_and_per_module() -> None:
    """A module improving while the repo improves must not read as degrading."""
    modules = [{"api": 40.0}] * 9 + [{"api": 80.0}]
    alerts = detect_trends(
        _runs([50.0] * 9 + [90.0], modules), window=10, degradation_threshold=5.0
    )

    per_module = [a for a in alerts if a.metric == "module_score"]
    assert per_module
    assert {a.direction for a in alerts} == {"improving"}


def test_falling_module_score_is_reported_as_degrading() -> None:
    modules = [{"api": 80.0}] * 9 + [{"api": 40.0}]
    alerts = detect_trends(
        _runs([70.0] * 10, modules), window=10, degradation_threshold=5.0
    )

    per_module = [a for a in alerts if a.metric == "module_score"]
    assert per_module
    assert per_module[0].module == "api"
    assert per_module[0].direction == "degrading"


# ---------------------------------------------------------------------------
# Thresholds and windows
# ---------------------------------------------------------------------------


def test_change_below_the_threshold_raises_nothing() -> None:
    alerts = detect_trends(_runs([70.0] * 9 + [72.0]), window=10, degradation_threshold=5.0)
    assert [a for a in alerts if a.metric == "health_score"] == []


def test_too_few_runs_raises_nothing() -> None:
    """A repository has to be scanned enough times before a trend is real."""
    assert detect_trends(_runs([50.0, 90.0]), window=10) == []


def test_a_module_missing_from_the_latest_run_is_not_compared() -> None:
    """A module that disappeared was deleted or renamed, not degraded."""
    modules = [{"api": 80.0, "legacy": 30.0}] * 9 + [{"api": 80.0}]
    alerts = detect_trends(
        _runs([70.0] * 10, modules), window=10, degradation_threshold=5.0
    )
    assert [a for a in alerts if a.module == "legacy"] == []


def test_alerts_carry_the_window_they_were_computed_over() -> None:
    alerts = detect_trends(_runs([90.0] * 9 + [50.0]), window=10, degradation_threshold=5.0)
    assert all(isinstance(a, TrendAlert) for a in alerts)
    assert all(a.window == 10 for a in alerts)
    # delta is a magnitude; the direction field carries the sign
    assert all(a.delta >= 0 for a in alerts)
