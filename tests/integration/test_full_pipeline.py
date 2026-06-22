import json
import shutil
from pathlib import Path
import pytest
from typer.testing import CliRunner
from archguard.cli.main import app

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
def test_init_creates_contract(git_repo):
    """archguard init should create a .archguard.yml in the project directory."""
    (git_repo / ".archguard.yml").unlink(missing_ok=True)

    result = runner.invoke(
        app,
        [
            "init",
            "--repo",
            str(git_repo),
            "--output",
            str(git_repo / ".archguard.yml"),
            "--no-llm",
            "--confirm-all",
            "--force-ci",
        ],
    )
    assert result.exit_code == 0, f"Init failed: {result.stdout}"
    assert (git_repo / ".archguard.yml").exists()


@pytest.mark.integration
def test_analyze_returns_score(git_repo):
    """archguard analyze --json should return a valid JSON report."""
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
    result = runner.invoke(
        app,
        [
            "analyze",
            "--repo",
            str(git_repo),
            "--changed-files",
            files,
            "--fail-on-warn",
            "--no-llm",
        ],
    )
    assert result.exit_code == 1, (
        f"Expected violations but got exit code {result.exit_code}. Output: {result.stdout}"
    )


@pytest.mark.integration
def test_status_after_analyze(git_repo):
    """archguard status should show results from the last run."""
    files = "api/routes.py,db/models.py,utils/helpers.py"
    runner.invoke(
        app, ["analyze", "--repo", str(git_repo), "--changed-files", files, "--no-llm"]
    )

    result = runner.invoke(app, ["status", "--repo", str(git_repo)])
    assert result.exit_code == 0, f"Status failed: {result.stdout}"
    stdout = result.stdout.lower()
    assert "schema version" in stdout
    assert "audit log:" in stdout


@pytest.mark.integration
def test_incremental_reads_from_cache_dir(git_repo):
    """Incremental mode should read previous violations from .archguard-cache/audit.jsonl"""
    from unittest.mock import patch
    from archguard.analysis.layers import AnalysisResult, ViolationDetail
    from archguard.analysis.scoring import ArchDebtResult, LayerScores
    from archguard.utils.severity import Severity
    from archguard.analysis.scoring import ArchDebtBand

    files = "api/routes.py,db/models.py,utils/helpers.py"

    fake_result = AnalysisResult(
        archdebt=ArchDebtResult(
            layer_scores=LayerScores(0.1, 0.1, 0.1, 0.2),
            composite_score=0.5,
            band=ArchDebtBand.WARN,
            should_fail_ci=False,
            weights=(0.25, 0.25, 0.25, 0.25),
            per_component_breach=False,
            composite_breach=False,
        ),
        violations=[
            ViolationDetail(
                layer=1,
                module="db",
                message="Bad import",
                commit_sha="1234567",
                file_path="db/models.py",
                severity=Severity.HIGH,
            )
        ],
        changed_files=["api/routes.py", "db/models.py", "utils/helpers.py"],
    )

    with patch(
        "archguard.cli._analyze_core.AnalysisOrchestrator.run", return_value=fake_result
    ):
        # 1. First run generates cache and audit log
        res1 = runner.invoke(
            app,
            [
                "analyze",
                "--repo",
                str(git_repo),
                "--changed-files",
                files,
                "--json",
                "--no-llm",
                "--incremental",
            ],
        )
        assert res1.exit_code in (0, 1), f"First analyze failed: {res1.stdout}"

        audit_log = git_repo / ".archguard-cache" / "audit.jsonl"
        assert audit_log.exists(), "Audit log should exist in .archguard-cache"

        # 2. Modify one file so it's changed, others unchanged
        (git_repo / "api" / "routes.py").write_text("import sys\n")

        fake_result2 = AnalysisResult(
            archdebt=ArchDebtResult(
                layer_scores=LayerScores(0.1, 0.0, 0.0, 0.0),
                composite_score=0.1,
                band=ArchDebtBand.HEALTHY,
                should_fail_ci=False,
                weights=(0.25, 0.25, 0.25, 0.25),
                per_component_breach=False,
                composite_breach=False,
            ),
            violations=[],
            changed_files=["api/routes.py"],
        )

    with patch(
        "archguard.cli._analyze_core.AnalysisOrchestrator.run",
        return_value=fake_result2,
    ):
        # 3. Second run incrementally
        res2 = runner.invoke(
            app,
            [
                "analyze",
                "--repo",
                str(git_repo),
                "--changed-files",
                files,
                "--json",
                "--no-llm",
                "--incremental",
            ],
        )
        assert res2.exit_code in (0, 1), f"Second analyze failed: {res2.stdout}"

        report1 = json.loads(res1.stdout)
        report2 = json.loads(res2.stdout)

        # Violations from db/models.py should be merged
        assert "violations" in report2

        merged_files = {
            v.get("file", v.get("module", "")) for v in report2["violations"]
        }
        assert "db/models.py" in merged_files, (
            "Violations from unchanged files were not merged correctly from the audit log."
        )
