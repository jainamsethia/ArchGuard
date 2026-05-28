import json
import shutil
from pathlib import Path
import pytest
from typer.testing import CliRunner
from archguard.cli.main import app

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_PROJECT = FIXTURES_DIR / "sample_project"

runner = CliRunner(mix_stderr=False)

@pytest.fixture
def git_repo(tmp_path):
    import subprocess
    repo_path = tmp_path / "repo"
    shutil.copytree(SAMPLE_PROJECT, repo_path)
    subprocess.run(["git", "init"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True)
    return repo_path

@pytest.mark.integration
def test_init_creates_contract(git_repo):
    """archguard init should create a .archguard.yml in the project directory."""
    (git_repo / ".archguard.yml").unlink(missing_ok=True)
    
    result = runner.invoke(app, [
        "init",
        "--repo", str(git_repo),
        "--output", str(git_repo / ".archguard.yml"),
        "--no-llm",
        "--confirm-all",
        "--force-ci"
    ])
    assert result.exit_code == 0, f"Init failed: {result.stdout}"
    assert (git_repo / ".archguard.yml").exists()

@pytest.mark.integration
def test_analyze_returns_score(git_repo):
    """archguard analyze --json should return a valid JSON report."""
    files = "api/routes.py,db/models.py,utils/helpers.py"
    result = runner.invoke(app, [
        "analyze",
        "--repo", str(git_repo),
        "--changed-files", files,
        "--json",
        "--no-llm"
    ])
    assert result.exit_code in (0, 1), f"Analyze failed: {result.stdout}"
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"Could not parse JSON output. stdout: {result.stdout}")
        
    assert "score" in report
    assert 0 <= report["score"] <= 100
    assert "violations" in report

@pytest.mark.integration
def test_analyze_exits_nonzero_on_violation(git_repo):
    """--fail-on-warn should cause non-zero exit when violations exist."""
    files = "api/routes.py,db/models.py,utils/helpers.py"
    result = runner.invoke(app, [
        "analyze",
        "--repo", str(git_repo),
        "--changed-files", files,
        "--fail-on-warn",
        "--no-llm"
    ])
    assert result.exit_code == 1, f"Expected violations but got exit code {result.exit_code}. Output: {result.stdout}"

@pytest.mark.integration
def test_status_after_analyze(git_repo):
    """archguard status should show results from the last run."""
    files = "api/routes.py,db/models.py,utils/helpers.py"
    runner.invoke(app, [
        "analyze",
        "--repo", str(git_repo),
        "--changed-files", files,
        "--no-llm"
    ])
    
    result = runner.invoke(app, [
        "status",
        "--repo", str(git_repo)
    ])
    assert result.exit_code == 0, f"Status failed: {result.stdout}"
    stdout = result.stdout.lower()
    assert "schema version" in stdout
    assert "audit log:" in stdout
