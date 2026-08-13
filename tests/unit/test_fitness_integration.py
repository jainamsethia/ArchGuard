"""Integration tests for Phase 3 Step 3: Fitness Functions."""

from archguard.analysis.scoring import ArchDebtResult, LayerScores, ArchDebtBand
from archguard.audit.logger import serialize_fitness_results
from archguard.config import FitnessFunctionConfig
from archguard.fitness.result import FitnessFunctionResult


def _create_base_archdebt() -> ArchDebtResult:
    """Create a healthy ArchDebtResult."""
    return ArchDebtResult(
        composite_score=0.1,
        band=ArchDebtBand.HEALTHY,
        layer_scores=LayerScores(0.0, 0.0, 0.0, 0.0),
        weights=(0.25, 0.25, 0.25, 0.25),
        per_component_breach=False,
        composite_breach=False,
        should_fail_ci=False,
        fail_reasons=[],
        fitness_results=[],
    )


def test_no_fitness_functions_config():
    """When no fitness functions exist, should_fail_ci is unchanged and fitness_passed is True."""
    archdebt = _create_base_archdebt()

    archdebt.apply_fitness_results([], [])

    assert archdebt.fitness_passed is True
    assert archdebt.should_fail_ci is False
    assert archdebt.fail_reasons == []


def test_fitness_functions_run_after_analysis():
    """All passed fitness functions should set fitness_passed=True and not fail CI."""
    archdebt = _create_base_archdebt()
    configs = [
        FitnessFunctionConfig(
            name="healthy", rule="health_score >= 80", severity="critical"
        )
    ]
    results = [FitnessFunctionResult(rule="health_score >= 80", passed=True)]

    archdebt.apply_fitness_results(results, configs)

    assert archdebt.fitness_passed is True
    assert archdebt.should_fail_ci is False
    assert archdebt.fail_reasons == []


def test_critical_fitness_failure_fails_ci():
    """A critical fitness failure flips should_fail_ci and sets fitness_passed=False."""
    archdebt = _create_base_archdebt()
    configs = [
        FitnessFunctionConfig(
            name="cycles", rule="graph.cycles == 0", severity="critical"
        )
    ]
    results = [
        FitnessFunctionResult(
            rule="graph.cycles == 0", passed=False, details="Cycle found: a -> b"
        )
    ]

    assert archdebt.should_fail_ci is False  # Before

    archdebt.apply_fitness_results(results, configs)

    assert archdebt.fitness_passed is False
    assert archdebt.should_fail_ci is True
    assert (
        "Fitness function 'cycles' FAILED (critical): Cycle found: a -> b"
        in archdebt.fail_reasons
    )


def test_warn_fitness_failure_does_not_fail_ci():
    """A warning-level fitness failure keeps should_fail_ci=False and fitness_passed=True."""
    archdebt = _create_base_archdebt()
    configs = [
        FitnessFunctionConfig(
            name="warn_rule", rule="module[x].fan_out <= 5", severity="warn"
        )
    ]
    results = [
        FitnessFunctionResult(
            rule="module[x].fan_out <= 5", passed=False, details="fan_out=10"
        )
    ]

    archdebt.apply_fitness_results(results, configs)

    assert archdebt.fitness_passed is True
    assert archdebt.should_fail_ci is False
    assert archdebt.fail_reasons == []  # Not appended for warnings


def test_fitness_results_in_audit_log():
    """serialize_fitness_results correctly structures data for the audit logger."""
    configs = [
        FitnessFunctionConfig(
            name="db_rule",
            rule="module[api] must not import module[db]",
            severity="critical",
            rationale="API isolates DB",
        ),
        FitnessFunctionConfig(
            name="info_rule", rule="health_score >= 90", severity="info"
        ),
    ]
    results = [
        FitnessFunctionResult(
            rule="module[api] must not import module[db]",
            passed=False,
            details="Violation!",
        ),
        FitnessFunctionResult(rule="health_score >= 90", passed=True),
    ]

    serialized = serialize_fitness_results(results, configs)

    assert len(serialized) == 2

    # First rule
    assert serialized[0]["name"] == "db_rule"
    assert serialized[0]["rule"] == "module[api] must not import module[db]"
    assert serialized[0]["passed"] is False
    assert serialized[0]["severity"] == "critical"
    assert serialized[0]["evidence"] == "Violation!"
    assert serialized[0]["rationale"] == "API isolates DB"

    # Second rule
    assert serialized[1]["name"] == "info_rule"
    assert serialized[1]["passed"] is True
    assert serialized[1]["severity"] == "info"
    assert serialized[1]["evidence"] == ""


def test_orchestrator_injects_fitness_metrics(tmp_path):
    """Verify that _run_orchestrator injects serialized fitness_results into result.metrics."""
    from archguard.analysis.layers import AnalysisOrchestrator

    # We create a dummy repo with an empty python file
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "main.py").write_text("print('hello')")

    # We mock the contract to include a fitness function
    contract = {
        "version": "3.0",
        "modules": [{"name": "app", "path": ""}],
        "fitness_functions": [
            {
                "name": "mock_rule",
                "rule": "health_score >= 0",
                "severity": "info",
                "rationale": "Always passes",
            }
        ],
    }

    # Use monkeypatch to override load_contract so Orchestrator uses our mock
    import archguard.analysis.layers

    original_load = archguard.analysis.layers.load_contract
    archguard.analysis.layers.load_contract = lambda root: contract

    try:
        orchestrator = AnalysisOrchestrator(repo_root)

        # Run orchestrator
        res = orchestrator.run([repo_root / "main.py"], "dummy_sha", quiet=True)

        # Verify fitness_results is populated in metrics for the audit log
        assert "fitness_results" in res.metrics
        assert len(res.metrics["fitness_results"]) == 1
        assert res.metrics["fitness_results"][0]["name"] == "mock_rule"
        assert res.metrics["fitness_results"][0]["passed"] is True
    finally:
        archguard.analysis.layers.load_contract = original_load


# ---------------------------------------------------------------------------
# Band/grade cap on a failed critical gate
#
# A repo with a real import cycle must not be presentable as grade A/B. The cap
# is applied to the *reported* band and grade only: composite_score stays the
# measured layer debt. The previous implementation floored the composite at a
# fixed 0.75, which reported an exact health score of 25.0 that no layer had
# measured.
# ---------------------------------------------------------------------------


def _critical_cycle_failure():
    configs = [
        FitnessFunctionConfig(
            name="cycles", rule="graph.cycles == 0", severity="critical"
        )
    ]
    results = [
        FitnessFunctionResult(
            rule="graph.cycles == 0", passed=False, details="Cycle found: a -> b -> a"
        )
    ]
    return results, configs


def test_critical_failure_caps_band_without_moving_the_score():
    archdebt = _create_base_archdebt()
    archdebt.fail_threshold = 0.25
    before = archdebt.composite_score

    archdebt.apply_fitness_results(*_critical_cycle_failure())

    assert archdebt.composite_score == before, (
        "composite must remain the measured layer debt; capping is a reporting "
        "decision, not a point deduction"
    )
    assert archdebt.health_score == round((1.0 - before) * 100, 1)
    assert archdebt.band is ArchDebtBand.CRITICAL


def test_critical_failure_caps_grade_below_b():
    """A healthy-scoring repo with a cycle must not report A or B."""
    archdebt = _create_base_archdebt()  # composite 0.1 -> health 90.0 -> "A"
    archdebt.fail_threshold = 0.25
    assert archdebt.health_grade == "A"  # before

    archdebt.apply_fitness_results(*_critical_cycle_failure())

    assert archdebt.health_grade not in ("A", "B")
    assert archdebt.health_grade == "C"


def test_strict_contract_caps_grade_further_than_the_floor():
    """The cap follows the contract's own fail_threshold when that is harsher."""
    archdebt = _create_base_archdebt()
    archdebt.fail_threshold = 0.75  # CRITICAL means health <= 25 -> "F"

    archdebt.apply_fitness_results(*_critical_cycle_failure())

    assert archdebt.health_grade == "F"


def test_passing_gates_do_not_cap_a_run_that_already_fails_ci():
    """Regression: the cap keyed off should_fail_ci, not off fitness results.

    A run that breached a layer threshold arrives with should_fail_ci already
    True. That must not cap the band when every fitness function passed.
    """
    archdebt = _create_base_archdebt()
    archdebt.should_fail_ci = True  # e.g. a per-component breach
    archdebt.band = ArchDebtBand.WATCH
    before = archdebt.composite_score

    configs = [
        FitnessFunctionConfig(
            name="cycles", rule="graph.cycles == 0", severity="critical"
        )
    ]
    results = [FitnessFunctionResult(rule="graph.cycles == 0", passed=True, details="")]
    archdebt.apply_fitness_results(results, configs)

    assert archdebt.fitness_passed is True
    assert archdebt.band is ArchDebtBand.WATCH
    assert archdebt.composite_score == before
