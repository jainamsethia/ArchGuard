import os
import json
import subprocess
import pytest
from typer.testing import CliRunner
from archguard.cli.main import app

@pytest.fixture
def fixture_repo(tmp_path):
    """Create a minimal Python repo with known boundary violations."""
    # Create src structure
    (tmp_path / "src" / "api").mkdir(parents=True)
    (tmp_path / "src" / "payments").mkdir(parents=True)
    # api module — clean
    (tmp_path / "src" / "api" / "__init__.py").write_text("from src.payments import process  # VIOLATION")
    # payments module — clean
    (tmp_path / "src" / "payments" / "__init__.py").write_text("def process(): return True")
    # .archguard.yml contract
    (tmp_path / ".archguard.yml").write_text("""
version: "3.0"
modules:
  - name: api
    paths: [src/api/]
    allowed_imports: []
  - name: payments
    paths: [src/payments/]
fail_threshold: 0.5
skip_layers: [semantic, duplication]
""")
    # Initialize git repo (required for pydriller/git diff)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True, 
                   env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t.com", "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t.com"})
    return tmp_path

def test_analyze_runs_without_crash(fixture_repo, tmp_path):
    runner = CliRunner()
    result = runner.invoke(app, [
        "analyze",
        "--repo", str(fixture_repo),
        "--dry-run",
        "--verbose",
        "--skip-explanation",
        "--out-file", str(tmp_path / "result.json")
    ], catch_exceptions=False)
    assert result.exit_code in (0, 1)  # 0=pass, 1=fail (both are valid outcomes)
    assert (tmp_path / "result.json").exists()
    
    data = json.loads((tmp_path / "result.json").read_text())
    assert "score" in data
    assert "band" in data
    assert data["band"] in ("PASS", "WARN", "FAIL")

def test_path_matching_no_false_positives(fixture_repo):
    """api_utils should NOT be assigned to the api module."""
    (fixture_repo / "src" / "api_utils").mkdir()
    (fixture_repo / "src" / "api_utils" / "helpers.py").write_text("x = 1")
    from archguard.analysis.coupling import _path_belongs_to_module
    assert not _path_belongs_to_module("src/api_utils/helpers.py", "src/api")
    assert _path_belongs_to_module("src/api/views.py", "src/api")
