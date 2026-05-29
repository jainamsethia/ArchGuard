"""Integration tests for global verbosity flags."""

import json
from pathlib import Path
import pytest
from typer.testing import CliRunner
import subprocess

from archguard.cli.main import app

runner = CliRunner()

def test_version_flag() -> None:
    """Test that --version outputs the version and exits."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "archguard, version " in result.stdout

def test_quiet_flag_suppresses_init(tmp_path: Path) -> None:
    """Test that --quiet suppresses info output for init."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test.py").write_text("def x(): pass")
    subprocess.run(["git", "init"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo, check=True)

    # Run without quiet (should see [1/5])
    result_normal = runner.invoke(app, ["init", "--repo", str(repo), "--confirm-all", "--no-llm", "--force-ci"])
    assert result_normal.exit_code == 0, result_normal.stdout
    assert "[1/5] Scanning repository" in result_normal.stdout

    # Run with quiet (should not see [1/5])
    repo2 = tmp_path / "repo2"
    repo2.mkdir()
    (repo2 / "test.py").write_text("def y(): pass")
    subprocess.run(["git", "init"], cwd=repo2, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo2, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo2, check=True)
    subprocess.run(["git", "add", "."], cwd=repo2, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo2, check=True)
    
    result_quiet = runner.invoke(app, ["--quiet", "init", "--repo", str(repo2), "--confirm-all", "--no-llm", "--force-ci"])
    assert result_quiet.exit_code == 0, result_quiet.stdout
    assert "[1/5] Scanning repository" not in result_quiet.stdout

def test_verbose_flag_shows_debug_init(tmp_path: Path) -> None:
    """Test that --verbose shows file assignments in init."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "test.py").write_text("def x(): pass")
    subprocess.run(["git", "init"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo, check=True)

    result = runner.invoke(app, ["--verbose", "init", "--repo", str(repo), "--confirm-all", "--no-llm", "--force-ci"])
    assert result.exit_code == 0, result.stdout
    assert "Found file test.py assigned to module" in result.stdout

def test_quiet_bypasses_prompt_in_suppress(tmp_path: Path, minimal_contract: dict) -> None:
    """Test that --quiet in suppress bypasses the prompt and exits 0."""
    import yaml
    repo = tmp_path / "repo"
    repo.mkdir()
    with (repo / ".archguard.yml").open("w") as f:
        yaml.dump(minimal_contract, f)

    # Need audit log for active violations
    from archguard.config import AUDIT_LOG_FILENAME
    audit_file = repo / AUDIT_LOG_FILENAME
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    with audit_file.open("w") as f:
        f.write(json.dumps({
            "timestamp": "2026-05-28T00:00:00Z",
            "event": "analysis_run",
            "violations": [{"module": "core", "layer": 1, "message": "test", "suppressed": False}]
        }) + "\n")

    result = runner.invoke(app, ["--quiet", "suppress", "add", "--all-pending", "--repo", str(repo)])
    assert result.exit_code == 0, result.stdout
    assert "Suppressed Violations" not in result.stdout

def test_quiet_suppresses_table_in_analyze(tmp_path: Path, minimal_contract: dict) -> None:
    """Test that --quiet in analyze suppresses the Rich report."""
    import yaml
    repo = tmp_path / "repo"
    repo.mkdir()
    with (repo / ".archguard.yml").open("w") as f:
        yaml.dump(minimal_contract, f)

    (repo / "src" / "core").mkdir(parents=True)
    (repo / "src" / "core" / "app.py").write_text("import os\n")

    result = runner.invoke(app, ["--quiet", "analyze", "--repo", str(repo), "--no-llm", "--changed-files", "src/core/app.py", "--no-incremental"])
    
    assert "ArchGuard Analysis" not in result.stdout
    assert "ArchDebt Score:" in result.stdout

def test_verbose_in_analyze(tmp_path: Path, minimal_contract: dict) -> None:
    """Test that --verbose shows file analysis steps in analyze."""
    import yaml
    repo = tmp_path / "repo"
    repo.mkdir()
    with (repo / ".archguard.yml").open("w") as f:
        yaml.dump(minimal_contract, f)

    (repo / "src" / "core").mkdir(parents=True)
    (repo / "src" / "core" / "app.py").write_text("import os\n")

    result = runner.invoke(app, ["--verbose", "analyze", "--repo", str(repo), "--no-llm", "--changed-files", "src/core/app.py", "--no-incremental"])
    
    assert "Analyzing 1 changed files..." in result.stdout
