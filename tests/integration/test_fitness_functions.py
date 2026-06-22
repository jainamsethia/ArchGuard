import json
from pathlib import Path
import pytest
from typer.testing import CliRunner
from archguard.cli.main import app
import shutil

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_PROJECT = FIXTURES_DIR / "sample_project"

runner = CliRunner()


@pytest.fixture
def git_repo(tmp_path):
    import subprocess

    repo_path = tmp_path / "repo"
    shutil.copytree(SAMPLE_PROJECT, repo_path)
    subprocess.run(["git", "init"], cwd=repo_path, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=repo_path, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True
    )
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True)
    return repo_path


@pytest.mark.integration
def test_fitness_functions_run_in_pipeline(git_repo):
    """run full analysis on sample_project, confirm fitness results exist and count."""
    files = "api/routes.py,db/models.py,utils/helpers.py"
    result = runner.invoke(
        app,
        [
            "analyze",
            "--repo",
            str(git_repo),
            "--changed-files",
            files,
            "--json",
            "--no-llm",
        ],
    )
    assert result.exit_code in (0, 1), f"Analyze failed: {result.stdout}"

    audit_log = git_repo / ".archguard-cache" / "audit.jsonl"
    assert audit_log.exists()
    lines = audit_log.read_text(encoding="utf-8").strip().split("\n")
    last_run = json.loads(lines[-1])
    fitness = last_run.get("metrics", {}).get("fitness_results", [])
    assert len(fitness) == 3


@pytest.mark.integration
def test_api_cannot_import_db_violated(git_repo):
    """sample_project api imports db, rule must fail."""
    # Ensure api/routes.py imports from db/models.py
    # From earlier inspection of sample_project, it might or might not already import db.
    # We will explicitly write an import to ensure it fails.
    (git_repo / "api" / "routes.py").write_text("from db import models\n")

    files = "api/routes.py"
    result = runner.invoke(
        app,
        [
            "analyze",
            "--repo",
            str(git_repo),
            "--changed-files",
            files,
            "--json",
            "--no-llm",
        ],
    )

    audit_log = git_repo / ".archguard-cache" / "audit.jsonl"
    lines = audit_log.read_text(encoding="utf-8").strip().split("\n")
    last_run = json.loads(lines[-1])
    fitness = last_run.get("metrics", {}).get("fitness_results", [])

    api_rule = next((r for r in fitness if r["name"] == "api_cannot_import_db"), None)
    assert api_rule is not None
    assert api_rule["passed"] is False


@pytest.mark.integration
def test_critical_fitness_failure_in_audit_log(git_repo):
    """run analysis, inspect audit log, verify fitness_results and critical failure recorded."""
    (git_repo / "api" / "routes.py").write_text("from db import models\n")
    files = "api/routes.py"

    runner.invoke(
        app,
        [
            "analyze",
            "--repo",
            str(git_repo),
            "--changed-files",
            files,
            "--json",
            "--no-llm",
        ],
    )

    audit_log = git_repo / ".archguard-cache" / "audit.jsonl"
    assert audit_log.exists()

    lines = audit_log.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) > 0
    last_run = json.loads(lines[-1])

    fitness = last_run.get("fitness_results")
    if not fitness and "metrics" in last_run:
        fitness = last_run["metrics"].get("fitness_results", [])

    assert fitness is not None, "fitness_results not found in audit log"
    api_rule = next((r for r in fitness if r["name"] == "api_cannot_import_db"), None)
    assert api_rule is not None
    assert api_rule["passed"] is False
    assert api_rule["severity"] == "critical"


@pytest.mark.integration
def test_fitness_check_cli_exit_code(git_repo):
    """Run fitness check CLI, verify exit code 1 and critical failure."""
    (git_repo / "api" / "routes.py").write_text("from db import models\n")

    result = runner.invoke(
        app,
        [
            "fitness",
            "check",
            "--repo",
            str(git_repo),
            "--json",
        ],
    )

    assert result.exit_code == 1

    output = json.loads(result.stdout)
    api_rule = next((r for r in output if r["name"] == "api_cannot_import_db"), None)
    assert api_rule is not None
    assert api_rule["passed"] is False
    assert api_rule["severity"] == "critical"
