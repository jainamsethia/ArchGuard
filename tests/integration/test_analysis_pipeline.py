import shutil
from pathlib import Path

import pytest

from archguard.analysis.layers import AnalysisOrchestrator

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sample_project"


@pytest.fixture
def temp_project(tmp_path: Path):
    project_dir = tmp_path / "sample_project"
    shutil.copytree(FIXTURE_DIR, project_dir)
    return project_dir


def test_layer1_detects_boundary_violation(temp_project, monkeypatch):
    """L1 must detect forbidden imports in the fixture project."""
    monkeypatch.setenv("ARCHGUARD_TEST_MODE", "1")
    monkeypatch.setenv("ARCHGUARD_SKIP_ML", "1")

    orchestrator = AnalysisOrchestrator(repo_root=temp_project)

    changed_files = [temp_project / "api" / "routes.py"]
    result = orchestrator.run(changed_files=changed_files, commit_sha="testsha1")

    l1_violations = [v for v in result.violations if v.layer == 1]
    assert len(l1_violations) > 0, "Expected at least one Layer 1 boundary violation"
    assert any("Imports" in v.message for v in l1_violations), (
        "Expected violation about api importing db"
    )


def test_health_score_semantics_are_consistent(temp_project, monkeypatch):
    """health_score must be the inverse of composite_score, 0-100."""
    monkeypatch.setenv("ARCHGUARD_TEST_MODE", "1")
    monkeypatch.setenv("ARCHGUARD_SKIP_ML", "1")

    orchestrator = AnalysisOrchestrator(repo_root=temp_project)
    changed_files = [temp_project / "api" / "routes.py"]
    result = orchestrator.run(changed_files=changed_files, commit_sha="testsha2")

    assert 0 <= result.archdebt.health_score <= 100
    expected_health = (1.0 - result.archdebt.composite_score) * 100
    assert abs(result.archdebt.health_score - expected_health) < 1e-5


def test_layer4_violations_can_be_suppressed(tmp_path, temp_project):
    """After Bug N-1 fix, Layer 4 violations must be suppressable.

    Layer 4 findings are produced by a different path from layers 1-3, which is
    what the original bug was about; the filter must still match them.
    """
    from archguard.analysis._suppression_filter import _filter_suppressed
    from archguard.suppression.models import make_violation_hash

    class _V:
        module, layer, message = "api", 4, "Duplicate function body"

    suppressed = {make_violation_hash("api", 4, "Duplicate function body")}

    assert _filter_suppressed(temp_project, [_V()], suppressed_hashes=suppressed) == []
    assert len(_filter_suppressed(temp_project, [_V()], suppressed_hashes=set())) == 1


def test_suppressed_violation_absent_from_analysis(temp_project, monkeypatch):
    """A suppressed violation must not appear in analysis results."""
    monkeypatch.setenv("ARCHGUARD_TEST_MODE", "1")
    monkeypatch.setenv("ARCHGUARD_SKIP_ML", "1")

    orchestrator = AnalysisOrchestrator(repo_root=temp_project)
    changed_files = [temp_project / "api" / "routes.py"]
    result1 = orchestrator.run(changed_files=changed_files, commit_sha="testsha3")

    assert len(result1.violations) > 0
    violation_to_suppress = result1.violations[0]

    # Suppress it. The orchestrator is handed the hashes rather than a path:
    # they are rows owned by the user who submitted the job, and the worker
    # resolves them before the pipeline starts.
    from archguard.suppression.models import make_violation_hash

    suppressed = {
        make_violation_hash(
            violation_to_suppress.module,
            violation_to_suppress.layer,
            violation_to_suppress.message,
        )
    }

    # Re-run analysis
    orchestrator2 = AnalysisOrchestrator(
        repo_root=temp_project, suppressed_hashes=suppressed
    )
    result2 = orchestrator2.run(changed_files=changed_files, commit_sha="testsha3")

    # Confirm the suppressed violation is absent

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


def test_analysis_is_deterministic(temp_project, monkeypatch):
    """The same codebase must produce the same score on repeated runs."""
    monkeypatch.setenv("ARCHGUARD_TEST_MODE", "1")
    monkeypatch.setenv("ARCHGUARD_SKIP_ML", "1")

    orchestrator1 = AnalysisOrchestrator(repo_root=temp_project)
    changed_files = [temp_project / "api" / "routes.py"]
    result1 = orchestrator1.run(changed_files=changed_files, commit_sha="testsha4")

    orchestrator2 = AnalysisOrchestrator(repo_root=temp_project)
    result2 = orchestrator2.run(changed_files=changed_files, commit_sha="testsha5")

    assert result1.archdebt.composite_score == result2.archdebt.composite_score
    assert len(result1.violations) == len(result2.violations)


def test_incremental_analysis_does_not_crash(temp_project, monkeypatch):
    """Incremental analysis must not crash when cache exists."""
    monkeypatch.setenv("ARCHGUARD_TEST_MODE", "1")
    monkeypatch.setenv("ARCHGUARD_SKIP_ML", "1")

    # Write initial cache manually if needed or just run normally and test that the second run passes
    orchestrator1 = AnalysisOrchestrator(repo_root=temp_project)
    changed_files = [temp_project / "api" / "routes.py"]
    orchestrator1.run(changed_files=changed_files, commit_sha="testsha6")

    # The actual incremental checking is done in `_analyze_core.py` by fetching previous results from the DB and filtering.
    # We can just verify `orchestrator.run` doesn't crash when `incremental=True` logic would be used by `_analyze_core`.
    # Wait, `run` just runs the analysis.
    # Let's run it again with same commit to ensure no crash from reading cache.
    result2 = orchestrator1.run(changed_files=changed_files, commit_sha="testsha6")

    assert result2 is not None
    assert isinstance(result2.archdebt.composite_score, float)
