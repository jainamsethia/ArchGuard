from pathlib import Path
from archguard.analysis.layers import AnalysisOrchestrator

def test_get_affected_modules_with_paths_and_module_names(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    
    # Mock a contract with both paths and module_names
    contract = {
        "modules": [
            {
                "name": "api",
                "paths": ["src/api/"],
            },
            {
                "name": "core",
                "module_names": ["src.core", "core"],
            },
            {
                "name": "utils",
                "paths": ["src/utils/"],
                "module_names": ["utils.helpers"]
            }
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
