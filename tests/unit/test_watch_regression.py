"""Deciding whether a rescan found something worth telling someone about.

Pure, and tested first, because this is where a watched repository earns or
loses its keep. Alert on noise and the notifications get muted; stay quiet on a
real regression and the feature did nothing. Both failures look the same from
the outside -- nobody acts on it -- so the threshold behaviour is pinned rather
than tuned by feel.

Built on `alerting.trend_detector`, deliberately, rather than a second
implementation that could disagree with it. The direction bug it once had
(rising health reported as "degrading") is C10, fixed in 0d77e38, and a test
here keeps it fixed from this side too.
"""

from __future__ import annotations

from archguard.watch import regression


def run(score, violations=None, fitness=None, run_id=1):
    """A persisted run, in the shape `store.run_to_dict` returns."""
    return {
        "id": run_id,
        "score": score,
        "violations": violations or [],
        "metrics": {"fitness_results": fitness or []},
    }


def violation(module="core", severity="high", message="fan_out=9 exceeds budget=3", layer=2):
    return {"module": module, "severity": severity, "message": message, "layer": layer}


# ------------------------------------------------------------- health drops


def test_a_health_drop_past_the_threshold_is_a_regression():
    result = regression.detect(run(90.0), run(80.0, run_id=2), threshold=5.0)
    assert result is not None
    assert result.kind == "health_drop"
    assert "90" in result.summary and "80" in result.summary


def test_a_drop_smaller_than_the_threshold_is_not():
    """Scores move a little between scans for reasons nobody needs paging for."""
    assert regression.detect(run(90.0), run(87.0, run_id=2), threshold=5.0) is None


def test_the_threshold_is_per_watch():
    """A repository under active refactoring and one in maintenance do not
    deserve the same sensitivity."""
    before, after = run(90.0), run(87.0, run_id=2)
    assert regression.detect(before, after, threshold=5.0) is None
    assert regression.detect(before, after, threshold=2.0) is not None


def test_improving_health_is_never_a_regression():
    """C10: a rising score was once reported as "degrading by 40". Nobody wants
    to be alerted that their code got better."""
    assert regression.detect(run(50.0), run(90.0, run_id=2), threshold=5.0) is None


def test_an_unchanged_score_is_not_a_regression():
    assert regression.detect(run(80.0), run(80.0, run_id=2), threshold=5.0) is None


# ---------------------------------------------------------- fitness gates


def test_a_newly_failing_gate_is_a_regression():
    """Independent of the score: a critical gate flipping to failing is exactly
    the event a watched repository exists to catch."""
    before = run(80.0, fitness=[{"name": "no_circular_deps", "passed": True}])
    after = run(80.0, fitness=[{"name": "no_circular_deps", "passed": False}], run_id=2)

    result = regression.detect(before, after, threshold=5.0)
    assert result is not None
    assert result.kind == "fitness_gate"
    assert "no_circular_deps" in result.summary


def test_a_gate_that_was_already_failing_is_not_news():
    """It was reported when it broke. Repeating it every day is how a feed
    becomes noise."""
    failing = [{"name": "no_circular_deps", "passed": False}]
    assert regression.detect(
        run(80.0, fitness=failing), run(80.0, fitness=failing, run_id=2), threshold=5.0
    ) is None


def test_a_gate_that_started_passing_is_not_a_regression():
    before = run(80.0, fitness=[{"name": "g", "passed": False}])
    after = run(80.0, fitness=[{"name": "g", "passed": True}], run_id=2)
    assert regression.detect(before, after, threshold=5.0) is None


# ------------------------------------------------------- severe violations


def test_a_new_critical_violation_is_a_regression():
    before = run(80.0, violations=[violation(severity="low")])
    after = run(80.0, violations=[violation(severity="low"), violation(module="api", severity="critical")], run_id=2)

    result = regression.detect(before, after, threshold=5.0)
    assert result is not None
    assert result.kind == "new_violation"
    assert "api" in result.summary


def test_a_pre_existing_critical_violation_is_not_news():
    same = [violation(severity="critical")]
    assert regression.detect(
        run(80.0, violations=same), run(80.0, violations=same, run_id=2), threshold=5.0
    ) is None


def test_a_new_low_severity_violation_is_not_alerted():
    """Watching is for regressions, not for every finding. A low-severity
    addition belongs in the dashboard, not in someone's notifications."""
    before = run(80.0, violations=[])
    after = run(80.0, violations=[violation(severity="low")], run_id=2)
    assert regression.detect(before, after, threshold=5.0) is None


def test_a_resolved_violation_is_not_a_regression():
    before = run(80.0, violations=[violation(severity="critical")])
    after = run(80.0, violations=[], run_id=2)
    assert regression.detect(before, after, threshold=5.0) is None


# --------------------------------------------------------------- first scan


def test_the_first_scan_of_a_repository_alerts_on_nothing():
    """There is nothing to compare against, and "your repository has problems"
    on the first scan is the dashboard's job, not an alert's."""
    assert regression.detect(None, run(10.0), threshold=5.0) is None


# ---------------------------------------------------- deterministic identity


def test_the_same_regression_yields_the_same_key():
    """The duplicate-alert guard. A worker that sent an alert and died before
    recording it must compute the same key on retry, or it sends it twice.
    """
    before, after = run(90.0), run(80.0, run_id=2)
    first = regression.detect(before, after, threshold=5.0)
    second = regression.detect(before, after, threshold=5.0)
    assert first.alert_key == second.alert_key
    assert len(first.alert_key) == 64, "expected a sha256 hex digest"


def test_a_different_run_yields_a_different_key():
    """Otherwise a second, genuine regression would be suppressed as a
    duplicate of the first."""
    first = regression.detect(run(90.0), run(80.0, run_id=2), threshold=5.0)
    second = regression.detect(run(80.0), run(70.0, run_id=3), threshold=5.0)
    assert first.alert_key != second.alert_key


def test_a_different_kind_on_the_same_run_yields_a_different_key():
    before = run(80.0, fitness=[{"name": "g", "passed": True}])
    after_gate = run(80.0, fitness=[{"name": "g", "passed": False}], run_id=2)
    after_drop = run(70.0, fitness=[{"name": "g", "passed": True}], run_id=2)

    gate = regression.detect(before, after_gate, threshold=5.0)
    drop = regression.detect(before, after_drop, threshold=5.0)
    assert gate.alert_key != drop.alert_key


# ------------------------------------------------------------- built on trends


def test_it_uses_the_existing_trend_detector():
    """Not a second implementation that could drift from the first.

    `detect_trends` guards on `len(runs) < window` with window=10, which for a
    watched repository would mean no alert until its tenth scan -- so the window
    is narrowed to the pair being compared, which is what the plan's I-6 note
    called for.
    """
    import inspect

    source = inspect.getsource(regression)
    assert "detect_trends" in source, "regression detection reimplements trends"
