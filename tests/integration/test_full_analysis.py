import pytest
from typer.testing import CliRunner
from archguard.cli.main import app
from pathlib import Path

runner = CliRunner()


@pytest.fixture
def sample_repo():
    return Path(__file__).parent.parent / "fixtures" / "sample_repo"


@pytest.fixture
def clean_repo(tmp_path):
    import subprocess

    # Create source structure
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "core").mkdir()
    (tmp_path / "src" / "core" / "module_a.py").write_text("def do_stuff(): pass\n")

    # Write contract with ML layers skipped
    (tmp_path / ".archguard.yml").write_text("""\
version: "3.0"
modules:
  - name: core
    path: src/core
skip_layers:
  - semantic
  - duplication
""")

    # Initialize git repo so changed-file detection works
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True
    )
    return tmp_path


def test_full_analysis_detects_boundary_violation(tmp_path, sample_repo):
    result = runner.invoke(
        app,
        [
            "analyze",
            "--repo",
            str(sample_repo),
            "--no-llm",
            "--changed-files",
            "src/core/module_a.py,src/api/module_b.py",
        ],
        env={"ARCHGUARD_SKIP_ML": "1"},
    )
    assert result.exit_code == 1  # violations found
    assert "boundary" in result.output.lower()


def test_full_analysis_clean_repo_exits_zero(tmp_path, clean_repo):
    result = runner.invoke(
        app,
        [
            "analyze",
            "--repo",
            str(clean_repo),
            "--no-llm",
            "--changed-files",
            "src/core/module_a.py",
        ],
        env={"ARCHGUARD_SKIP_ML": "1"},
    )
    assert result.exit_code == 0
