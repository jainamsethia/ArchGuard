import pytest
from datetime import datetime, timezone
from archguard.evolution.tracker import EvolutionTracker
from archguard.evolution.models import TrendClassification


def test_empty_history():
    tracker = EvolutionTracker([])
    report = tracker.generate_report()

    assert len(report.snapshots) == 0
    assert report.health_trend.classification == TrendClassification.STABLE
    assert report.health_trend.current_value == 0.0
    assert report.violation_trend.classification == TrendClassification.STABLE
    assert report.debt_trend.classification == TrendClassification.STABLE
    assert report.fitness_trend is None


def test_single_snapshot():
    raw = [
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "score": 85.0,
            "violations": [{"id": 1}, {"id": 2}],
            "metrics": {"fitness_results": [{"passed": True}, {"passed": False}]},
        }
    ]

    tracker = EvolutionTracker(raw)
    report = tracker.generate_report()

    assert len(report.snapshots) == 1
    assert report.health_trend.classification == TrendClassification.STABLE
    assert report.health_trend.current_value == 85.0
    assert report.violation_trend.current_value == 2.0
    assert report.debt_trend.current_value == pytest.approx(0.15)

    assert report.fitness_trend is not None
    assert report.fitness_trend.current_value == 0.5
    assert report.fitness_trend.classification == TrendClassification.STABLE


def test_multiple_snapshots_improving():
    raw = [
        {
            "timestamp": "2023-01-01T10:00:00Z",
            "score": 70.0,
            "violations": [1, 2, 3],
            "metrics": {"fitness_results": [{"passed": False}, {"passed": False}]},
        },
        {
            "timestamp": "2023-01-02T10:00:00Z",
            "score": 85.0,
            "violations": [1],
            "metrics": {"fitness_results": [{"passed": True}, {"passed": False}]},
        },
    ]

    tracker = EvolutionTracker(raw)
    report = tracker.generate_report()

    assert len(report.snapshots) == 2

    # Health should improve (higher is better)
    assert report.health_trend.classification == TrendClassification.IMPROVING
    assert report.health_trend.delta == 15.0

    # Violations should improve (lower is better)
    assert report.violation_trend.classification == TrendClassification.IMPROVING
    assert report.violation_trend.delta == -2.0

    # Debt should improve (lower is better)
    assert report.debt_trend.classification == TrendClassification.IMPROVING
    assert report.debt_trend.delta == pytest.approx(-0.15)

    # Fitness should improve (ratio 0.0 -> 0.5)
    assert report.fitness_trend is not None
    assert report.fitness_trend.classification == TrendClassification.IMPROVING
    assert report.fitness_trend.delta == 0.5


def test_multiple_snapshots_declining():
    raw = [
        {
            "timestamp": "2023-01-01T10:00:00Z",
            "score": 85.0,
            "violations": [1],
            "metrics": {"fitness_results": [{"passed": True}, {"passed": False}]},
        },
        {
            "timestamp": "2023-01-02T10:00:00Z",
            "score": 70.0,
            "violations": [1, 2, 3],
            "metrics": {"fitness_results": [{"passed": False}, {"passed": False}]},
        },
    ]

    tracker = EvolutionTracker(raw)
    report = tracker.generate_report()

    assert report.health_trend.classification == TrendClassification.DECLINING
    assert report.violation_trend.classification == TrendClassification.DECLINING
    assert report.debt_trend.classification == TrendClassification.DECLINING

    assert report.fitness_trend is not None
    assert report.fitness_trend.classification == TrendClassification.DECLINING


def test_multiple_snapshots_stable():
    raw = [
        {
            "timestamp": "2023-01-01T10:00:00Z",
            "score": 85.0,
            "violations": [1],
            "metrics": {"fitness_results": [{"passed": True}, {"passed": False}]},
        },
        {
            "timestamp": "2023-01-02T10:00:00Z",
            "score": 85.0,
            "violations": [1],
            "metrics": {"fitness_results": [{"passed": True}, {"passed": False}]},
        },
    ]

    tracker = EvolutionTracker(raw)
    report = tracker.generate_report()

    assert report.health_trend.classification == TrendClassification.STABLE
    assert report.violation_trend.classification == TrendClassification.STABLE
    assert report.debt_trend.classification == TrendClassification.STABLE

    assert report.fitness_trend is not None
    assert report.fitness_trend.classification == TrendClassification.STABLE


def test_invalid_snapshot_skipped():
    raw = [{"invalid": "data"}, {"timestamp": "2023-01-01T10:00:00Z", "score": 80.0}]
    tracker = EvolutionTracker(raw)
    assert len(tracker.snapshots) == 1
    assert tracker.snapshots[0].health_score == 80.0
