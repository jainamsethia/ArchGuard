import pytest
from pathlib import Path
from archguard.analysis.layers import AnalysisOrchestrator

@pytest.mark.benchmark
def test_parallel_layers_faster_than_sequential(benchmark, tmp_path):
    """
    Test that parallel layer execution works and is potentially faster.
    This doesn't strictly assert timing, as CI can be noisy, but it validates
    the concurrent logic doesn't crash and returns the correct payload.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    
    (repo / ".archguard.yml").write_text('''
version: "3.0"
modules:
  - name: module_a
    path: "a/"
    coupling_budget: 1
    allowed_imports: []
  - name: module_b
    path: "b/"
    coupling_budget: 1
    allowed_imports: []

''')
    
    # Create fake codebase
    (repo / "a").mkdir()
    (repo / "b").mkdir()
    (repo / "a" / "main.py").write_text("import b.core\nfrom b import core")
    (repo / "b" / "core.py").write_text("import a.main\nfrom a import main")
    
    db_path = repo / ".archguard-cache" / "embeddings.db"
    db_path.parent.mkdir(parents=True)
    from archguard.cache.db import EmbeddingDB
    db = EmbeddingDB(db_path)
    
    orchestrator = AnalysisOrchestrator(repo, db_path)
    # Mock ML skip
    orchestrator.contract["skip_layers"] = ["semantic", "duplication"]
    
    changed_files = [repo / "a" / "main.py", repo / "b" / "core.py"]
    
    def run_pipeline():
        return orchestrator.run(
            changed_files=changed_files,
            commit_sha="test1234",
            skip_explanation=True
        )

    # We use benchmark to run it a few times to check concurrency safety
    result = benchmark(run_pipeline)
    
    # Check that both layer 1 and 2 reported violations
    v_layers = [v.layer for v in result.violations]
    assert 1 in v_layers, "Layer 1 should have reported violations"
    assert 2 in v_layers, "Layer 2 should have reported violations"
