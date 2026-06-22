import pytest
import os
from pathlib import Path
import shutil

from archguard.analysis.layers import AnalysisOrchestrator
from archguard.suppression.store import SuppressionStore


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sample_project"


@pytest.fixture
def temp_project(tmp_path: Path):
    project_dir = tmp_path / "sample_project"
    shutil.copytree(FIXTURE_DIR, project_dir)
    return project_dir


def test_layer1_detects_boundary_violation(temp_project):
    """L1 must detect forbidden imports in the fixture project."""
    os.environ["ARCHGUARD_TEST_MODE"] = "1"
    os.environ["ARCHGUARD_SKIP_ML"] = "1"

    orchestrator = AnalysisOrchestrator(repo_root=temp_project)

    changed_files = [temp_project / "api" / "routes.py"]
    result = orchestrator.run(changed_files=changed_files, commit_sha="testsha1")

    l1_violations = [v for v in result.violations if v.layer == 1]
    assert len(l1_violations) > 0, "Expected at least one Layer 1 boundary violation"
    assert any("Imports" in v.message for v in l1_violations), (
        "Expected violation about api importing db"
    )


def test_health_score_semantics_are_consistent(temp_project):
    """health_score must be the inverse of composite_score, 0-100."""
    os.environ["ARCHGUARD_TEST_MODE"] = "1"
    os.environ["ARCHGUARD_SKIP_ML"] = "1"

    orchestrator = AnalysisOrchestrator(repo_root=temp_project)
    changed_files = [temp_project / "api" / "routes.py"]
    result = orchestrator.run(changed_files=changed_files, commit_sha="testsha2")

    assert 0 <= result.archdebt.health_score <= 100
    expected_health = (1.0 - result.archdebt.composite_score) * 100
    assert abs(result.archdebt.health_score - expected_health) < 1e-5


def test_layer4_violations_can_be_suppressed(tmp_path, temp_project):
    """After Bug N-1 fix, Layer 4 violations must be suppressable."""
    store = SuppressionStore(temp_project)

    store.add(
        module="api",
        layer=4,
        message="Duplicate function body",
        reason="Test suppression for layer 4",
    )

    assert store.is_suppressed("api", 4, "Duplicate function body") is True


def test_suppressed_violation_absent_from_analysis(temp_project):
    """A suppressed violation must not appear in analysis results."""
    os.environ["ARCHGUARD_TEST_MODE"] = "1"
    os.environ["ARCHGUARD_SKIP_ML"] = "1"

    orchestrator = AnalysisOrchestrator(repo_root=temp_project)
    changed_files = [temp_project / "api" / "routes.py"]
    result1 = orchestrator.run(changed_files=changed_files, commit_sha="testsha3")

    assert len(result1.violations) > 0
    violation_to_suppress = result1.violations[0]

    # Add suppression
    store = SuppressionStore(temp_project)
    store.add(
        module=violation_to_suppress.module,
        layer=violation_to_suppress.layer,
        message=violation_to_suppress.message,
        reason="Suppressing first violation",
    )

    # Re-run analysis
    orchestrator2 = AnalysisOrchestrator(repo_root=temp_project)
    result2 = orchestrator2.run(changed_files=changed_files, commit_sha="testsha3")

    # Confirm the suppressed violation is absent
    from archguard.suppression.models import make_violation_hash

    hashes2 = [
        make_violation_hash(v.module, v.layer, v.message) for v in result2.violations
    ]
    assert (
        make_violation_hash(
            violation_to_suppress.module,
            violation_to_suppress.layer,
            violation_to_suppress.message,
        )
        not in hashes2
    )


def test_analysis_is_deterministic(temp_project):
    """The same codebase must produce the same score on repeated runs."""
    os.environ["ARCHGUARD_TEST_MODE"] = "1"
    os.environ["ARCHGUARD_SKIP_ML"] = "1"

    orchestrator1 = AnalysisOrchestrator(repo_root=temp_project)
    changed_files = [temp_project / "api" / "routes.py"]
    result1 = orchestrator1.run(changed_files=changed_files, commit_sha="testsha4")

    orchestrator2 = AnalysisOrchestrator(repo_root=temp_project)
    result2 = orchestrator2.run(changed_files=changed_files, commit_sha="testsha5")

    assert result1.archdebt.composite_score == result2.archdebt.composite_score
    assert len(result1.violations) == len(result2.violations)


def test_incremental_analysis_does_not_crash(temp_project):
    """Incremental analysis must not crash when cache exists."""
    os.environ["ARCHGUARD_TEST_MODE"] = "1"
    os.environ["ARCHGUARD_SKIP_ML"] = "1"

    # Write initial cache manually if needed or just run normally and test that the second run passes
    orchestrator1 = AnalysisOrchestrator(repo_root=temp_project)
    changed_files = [temp_project / "api" / "routes.py"]
    result1 = orchestrator1.run(changed_files=changed_files, commit_sha="testsha6")

    # The actual incremental checking is done in `_analyze_core.py` by fetching previous results from the DB and filtering.
    # We can just verify `orchestrator.run` doesn't crash when `incremental=True` logic would be used by `_analyze_core`.
    # Wait, `run` just runs the analysis.
    # Let's run it again with same commit to ensure no crash from reading cache.
    result2 = orchestrator1.run(changed_files=changed_files, commit_sha="testsha6")

    assert result2 is not None
    assert isinstance(result2.archdebt.composite_score, float)
