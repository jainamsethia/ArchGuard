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
    # A clean repo has no violations
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "core").mkdir()
    (tmp_path / "src" / "core" / "module_a.py").write_text("def do_stuff(): pass")

    (tmp_path / ".archguard.yml").write_text("""
version: "3.0"
modules:
  - name: core
    path: src/core
""")
    return tmp_path


def test_full_analysis_detects_boundary_violation(tmp_path, sample_repo):
    result = runner.invoke(app, ["analyze", "--repo", str(sample_repo), "--all"])
    assert result.exit_code == 1  # violations found
    assert "boundary" in result.output.lower()


def test_full_analysis_clean_repo_exits_zero(tmp_path, clean_repo):
    result = runner.invoke(app, ["analyze", "--repo", str(clean_repo)])
    assert result.exit_code == 0
