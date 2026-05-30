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

    affected = orchestrator._get_affected_modules(changed_files)

    assert "api" in affected
    assert api_file in affected["api"]

    assert "core" in affected
    assert core_file in affected["core"]

    assert "utils" in affected
    assert util_file1 in affected["utils"]  # Matched via paths
    assert util_file2 in affected["utils"]  # Matched via module_names

    assert "unknown" not in affected


def test_drift_computed_once_per_run(tmp_path):
    from unittest.mock import patch, MagicMock
    from archguard.analysis.semantic import SemanticDriftResult
    import numpy as np

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
            "archguard.analysis.layers.AnalysisOrchestrator._run_layer1",
            return_value=0.0,
        ),
        patch(
            "archguard.analysis.layers.AnalysisOrchestrator._run_layer2",
            return_value=0.0,
        ),
        patch(
            "archguard.analysis.layers.AnalysisOrchestrator._run_layer4",
            return_value=0.0,
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
