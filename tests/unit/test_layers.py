from archguard.analysis._orchestrator_utils import _get_affected_modules
from archguard.analysis._suppression_filter import _filter_suppressed
from archguard.analysis.layers import AnalysisOrchestrator


def test_get_affected_modules_with_paths_and_module_names(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    # Mock a contract with both paths and module_names
    contract = {
        "modules": [
            {
                "name": "api",
                "path": "src/api/",
            },
            {
                "name": "core",
                "module_names": ["src.core", "core"],
            },
            {"name": "utils", "path": "src/utils/", "module_names": ["utils.helpers"]},
        ]
    }

    class MockOrchestrator(AnalysisOrchestrator):
        def __init__(self):
            self.repo_root = repo_root
            self.contract = contract

    orchestrator = MockOrchestrator()

    # Create test files
    api_file = repo_root / "src" / "api" / "views.py"
    core_file = repo_root / "src" / "core" / "engine.py"
    util_file1 = repo_root / "src" / "utils" / "formatter.py"
    util_file2 = repo_root / "utils" / "helpers" / "string.py"
    unknown_file = repo_root / "unknown" / "file.py"

    changed_files = [api_file, core_file, util_file1, util_file2, unknown_file]

    affected = _get_affected_modules(
        orchestrator.repo_root, orchestrator.contract, changed_files
    )

    assert "api" in affected
    assert api_file in affected["api"]

    assert "core" in affected
    assert core_file in affected["core"]

    assert "utils" in affected
    assert util_file1 in affected["utils"]  # Matched via paths
    assert util_file2 in affected["utils"]  # Matched via module_names

    assert "unknown" not in affected


def test_drift_computed_once_per_run(tmp_path):
    from unittest.mock import MagicMock, patch

    import numpy as np

    from archguard.analysis.semantic import SemanticDriftResult

    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    contract = {
        "modules": [
            {
                "name": "core",
                "path": "src/core/",
            }
        ]
    }

    class MockOrchestrator(AnalysisOrchestrator):
        def __init__(self):
            self.repo_root = repo_root
            self.contract = contract
            self.db = MagicMock()
            self.cache = MagicMock()
            self._audit = None

    orchestrator = MockOrchestrator()
    core_file = repo_root / "src" / "core" / "test.py"
    core_file.parent.mkdir(parents=True)
    core_file.touch()

    fake_result = SemanticDriftResult(
        module_name="core",
        drift_score=0.4,  # > 0.25 to trigger proposal
        pre_pr_centroid=np.zeros(384, dtype=np.float32),
        post_pr_centroid=np.zeros(384, dtype=np.float32),
        functions_analyzed=1,
        cache_hit=False,
    )

    with (
        patch(
            "archguard.analysis._layer_runners._run_layer1",
            return_value=(0.0, []),
        ),
        patch(
            "archguard.analysis._layer_runners._run_layer2",
            return_value=(0.0, []),
        ),
        patch(
            "archguard.analysis._layer_runners._run_layer4",
            return_value=(0.0, [], ""),
        ),
        patch(
            "archguard.analysis.semantic.SemanticAnalyzer.compute_drift",
            return_value=fake_result,
        ) as mock_drift,
        patch(
            "archguard.contract.reinference.ReinferenceEngine.create_proposal"
        ) as mock_propose,
        patch("builtins.print"),
    ):
        orchestrator.run([core_file], "fake_sha")

        assert mock_drift.call_count == 1, "compute_drift must be called exactly once"
        assert mock_propose.call_count == 1, "create_proposal should have been called"


def test_get_module_paths():
    from archguard.analysis.layers import _get_module_paths

    assert _get_module_paths({"name": "core", "path": "src/"}) == ["src/"]
    assert _get_module_paths({"name": "core", "path": ["src/", "lib/"]}) == [
        "src/",
        "lib/",
    ]
    assert _get_module_paths({"name": "core", "paths": ["src/"]}) == ["src/"]


def test_analysis_orchestrator_context_manager(tmp_path):
    import sqlite3

    import pytest

    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    contract_file = repo_root / ".archguard.yml"
    contract_file.write_text(
        "version: '3.0'\nmodules:\n  - name: test\n    path: src/test\n"
    )

    db_path = tmp_path / "test.db"

    from archguard.analysis.layers import AnalysisOrchestrator

    with AnalysisOrchestrator(repo_root, db_path=db_path) as orchestrator:
        assert hasattr(orchestrator, "db")
        assert orchestrator.db is not None
        assert orchestrator.db.count_embeddings() >= 0
        db_instance = orchestrator.db

    with pytest.raises(
        sqlite3.ProgrammingError, match="Cannot operate on a closed database"
    ):
        db_instance.count_embeddings()


def test_filter_suppressed_layer_4(tmp_path):
    from unittest.mock import patch

    from archguard.analysis._models import Severity
    from archguard.analysis.layers import AnalysisOrchestrator, ViolationDetail

    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    contract = {"modules": []}

    class MockOrchestrator(AnalysisOrchestrator):
        def __init__(self):
            self.repo_root = repo_root
            self.contract = contract

    orchestrator = MockOrchestrator()

    violation = ViolationDetail(
        module="api",
        layer=4,
        message="Duplicate function body",
        severity=Severity.HIGH,
        file_path="src/api.py",
        commit_sha="abcd123",
    )

    with patch("archguard.suppression.store.SuppressionStore") as mock_store_cls:
        mock_store = mock_store_cls.return_value
        mock_store.is_suppressed.return_value = True

        filtered = _filter_suppressed(orchestrator.repo_root, [violation])

        assert len(filtered) == 0
        mock_store.is_suppressed.assert_called_once_with(
            "api", 4, "Duplicate function body"
        )
