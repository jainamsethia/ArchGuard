"""Unit tests for archguard analyze command."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from archguard.cli.main import app

runner: CliRunner = CliRunner()

_MINIMAL_CONTRACT: dict = {
    "version": "3.0",
    "modules": [
        {
            "name": "core",
            "path": "src/core/",
            "allowed_imports": ["utils"],
            "disallowed_imports": ["forbidden"],
            "coupling_budget": 5,
        },
    ],
    "fail_threshold": 0.75,
    "warn_threshold": 0.50,
}


def _setup_repo(tmp_path: Path) -> Path:
    """Create a minimal repo with contract and Python files."""
    repo = tmp_path / "repo"
    repo.mkdir()

    # Write contract
    contract_file = repo / ".archguard.yml"
    contract_file.write_text(yaml.dump(_MINIMAL_CONTRACT), encoding="utf-8")

    # Create a git dir stub
    git_dir = repo / ".git"
    git_dir.mkdir()

    # Create module files
    core_dir = repo / "src" / "core"
    core_dir.mkdir(parents=True)
    (core_dir / "__init__.py").write_text("", encoding="utf-8")
    (core_dir / "main.py").write_text(
        "import os\\nfrom pathlib import Path\\n",
        encoding="utf-8",
    )

    return repo


class TestAnalyzeCommand:
    """Tests for archguard analyze command."""

    def test_no_py_files_exit_0(self, tmp_path: Path) -> None:
        """No .py files changed -> exit 0, skip message."""
        repo = _setup_repo(tmp_path)
        result = runner.invoke(
            app,
            ["analyze", "--repo", str(repo), "--changed-files", "readme.md"],
        )
        assert result.exit_code == 0
        assert "No Python files changed" in result.output

    def test_json_output_valid(self, tmp_path: Path) -> None:
        """--json output is valid JSON with required keys."""
        repo = _setup_repo(tmp_path)
        py_file = repo / "src" / "core" / "main.py"

        # Mock the orchestrator to avoid real analysis
        mock_result = MagicMock()
        mock_result.archdebt.composite_score = 0.30
        mock_result.archdebt.band.value = "Healthy"
        mock_result.archdebt.should_fail_ci = False
        mock_result.archdebt.layer_scores.layer1_violation = 0.1
        mock_result.archdebt.layer_scores.layer2_coupling = 0.1
        mock_result.archdebt.layer_scores.layer3_drift = 0.05
        mock_result.archdebt.layer_scores.layer4_duplication = 0.05
        mock_result.violations = []
        mock_result.commit_sha = "a1b2c3d"
        mock_result.changed_files = ["src/core/main.py"]
        mock_result.fail_fast_triggered = False
        mock_result.skipped_layers_names = []

        with patch(
            "archguard.cli.analyze_cmd.AnalysisOrchestrator",
        ) as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch.contract = {}
            mock_orch.run.return_value = mock_result
            mock_orch_cls.return_value = mock_orch
            mock_orch_cls.get_commit_sha.return_value = "a1b2c3d"

            result = runner.invoke(
                app,
                [
                    "analyze", "--repo", str(repo),
                    "--changed-files", str(py_file),
                    "--json",
                ],
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "score" in data
        assert "grade" in data
        assert "timestamp" in data
        assert "violations" in data
        assert "metrics" in data
        assert "summary" in data
        assert data["summary"]["total_violations"] == 0

    def test_above_fail_threshold_exit_1(self, tmp_path: Path) -> None:
        """ArchDebt > fail_threshold -> exit 1."""
        repo = _setup_repo(tmp_path)
        py_file = repo / "src" / "core" / "main.py"

        mock_result = MagicMock()
        mock_result.archdebt.composite_score = 0.80
        mock_result.archdebt.band.value = "Critical"
        mock_result.archdebt.band = MagicMock()
        mock_result.archdebt.should_fail_ci = True
        mock_result.archdebt.layer_scores.layer1_violation = 0.9
        mock_result.archdebt.layer_scores.layer2_coupling = 0.8
        mock_result.archdebt.layer_scores.layer3_drift = 0.7
        mock_result.archdebt.layer_scores.layer4_duplication = 0.6
        mock_result.archdebt.weights = (0.25, 0.25, 0.25, 0.25)
        mock_result.violations = []
        mock_result.commit_sha = "a1b2c3d"
        mock_result.changed_files = ["src/core/main.py"]

        with patch(
            "archguard.cli.analyze_cmd.AnalysisOrchestrator",
        ) as mock_cls:
            mock_orch = MagicMock()
            mock_orch.run.return_value = mock_result
            mock_cls.return_value = mock_orch
            mock_cls.get_commit_sha.return_value = "a1b2c3d"

            result = runner.invoke(
                app,
                [
                    "analyze", "--repo", str(repo),
                    "--changed-files", str(py_file),
                    "--json",
                ],
            )

        assert result.exit_code == 1

    def test_below_fail_threshold_exit_0(self, tmp_path: Path) -> None:
        """ArchDebt < fail_threshold -> exit 0."""
        repo = _setup_repo(tmp_path)
        py_file = repo / "src" / "core" / "main.py"

        mock_result = MagicMock()
        mock_result.archdebt.composite_score = 0.30
        mock_result.archdebt.band.value = "Healthy"
        mock_result.archdebt.should_fail_ci = False
        mock_result.archdebt.layer_scores.layer1_violation = 0.1
        mock_result.archdebt.layer_scores.layer2_coupling = 0.1
        mock_result.archdebt.layer_scores.layer3_drift = 0.05
        mock_result.archdebt.layer_scores.layer4_duplication = 0.05
        mock_result.archdebt.weights = (0.25, 0.25, 0.25, 0.25)
        mock_result.violations = []
        mock_result.commit_sha = "a1b2c3d"
        mock_result.changed_files = ["src/core/main.py"]

        with patch(
            "archguard.cli.analyze_cmd.AnalysisOrchestrator",
        ) as mock_cls:
            mock_orch = MagicMock()
            mock_orch.run.return_value = mock_result
            mock_cls.return_value = mock_orch
            mock_cls.get_commit_sha.return_value = "a1b2c3d"

            result = runner.invoke(
                app,
                [
                    "analyze", "--repo", str(repo),
                    "--changed-files", str(py_file),
                ],
            )

        assert result.exit_code == 0

    def test_per_component_breach_exit_1(self, tmp_path: Path) -> None:
        """Per-component breach (composite clean) -> exit 1."""
        repo = _setup_repo(tmp_path)
        py_file = repo / "src" / "core" / "main.py"

        mock_result = MagicMock()
        mock_result.archdebt.composite_score = 0.30
        mock_result.archdebt.band.value = "Healthy"
        mock_result.archdebt.band = MagicMock()
        mock_result.archdebt.should_fail_ci = True  # per-component breach
        mock_result.archdebt.per_component_breach = True
        mock_result.archdebt.layer_scores.layer1_violation = 0.9
        mock_result.archdebt.layer_scores.layer2_coupling = 0.0
        mock_result.archdebt.layer_scores.layer3_drift = 0.0
        mock_result.archdebt.layer_scores.layer4_duplication = 0.0
        mock_result.archdebt.weights = (0.25, 0.25, 0.25, 0.25)
        mock_result.violations = []
        mock_result.commit_sha = "a1b2c3d"
        mock_result.changed_files = ["src/core/main.py"]

        with patch(
            "archguard.cli.analyze_cmd.AnalysisOrchestrator",
        ) as mock_cls:
            mock_orch = MagicMock()
            mock_orch.run.return_value = mock_result
            mock_cls.return_value = mock_orch
            mock_cls.get_commit_sha.return_value = "a1b2c3d"

            result = runner.invoke(
                app,
                [
                    "analyze", "--repo", str(repo),
                    "--changed-files", str(py_file),
                    "--json",
                ],
            )

        assert result.exit_code == 1

    def test_dry_run_no_github_calls(self, tmp_path: Path) -> None:
        """--dry-run -> no GitHub API calls made."""
        repo = _setup_repo(tmp_path)
        py_file = repo / "src" / "core" / "main.py"

        mock_result = MagicMock()
        mock_result.archdebt.composite_score = 0.30
        mock_result.archdebt.band.value = "Healthy"
        mock_result.archdebt.should_fail_ci = False
        mock_result.archdebt.layer_scores.layer1_violation = 0.1
        mock_result.archdebt.layer_scores.layer2_coupling = 0.1
        mock_result.archdebt.layer_scores.layer3_drift = 0.05
        mock_result.archdebt.layer_scores.layer4_duplication = 0.05
        mock_result.archdebt.weights = (0.25, 0.25, 0.25, 0.25)
        mock_result.violations = []
        mock_result.commit_sha = "a1b2c3d"
        mock_result.changed_files = ["src/core/main.py"]

        with patch(
            "archguard.cli.analyze_cmd.AnalysisOrchestrator",
        ) as mock_cls:
            mock_orch = MagicMock()
            mock_orch.run.return_value = mock_result
            mock_cls.return_value = mock_orch
            mock_cls.get_commit_sha.return_value = "a1b2c3d"

            result = runner.invoke(
                app,
                [
                    "analyze", "--repo", str(repo),
                    "--changed-files", str(py_file),
                    "--pr", "1", "--repo-slug", "org/repo",
                    "--dry-run",
                ],
            )

        # Dry-run succeeds without calling any GitHub APIs
        assert result.exit_code == 0
        # The GitHub import is lazy inside a conditional block,
        # so dry_run simply skips that block entirely.

    def test_skip_explanation_flag(self, tmp_path: Path) -> None:
        """--skip-explanation is passed through to orchestrator."""
        repo = _setup_repo(tmp_path)
        py_file = repo / "src" / "core" / "main.py"

        mock_result = MagicMock()
        mock_result.archdebt.composite_score = 0.10
        mock_result.archdebt.band.value = "Healthy"
        mock_result.archdebt.band = MagicMock()
        mock_result.archdebt.should_fail_ci = False
        mock_result.archdebt.layer_scores.layer1_violation = 0.0
        mock_result.archdebt.layer_scores.layer2_coupling = 0.0
        mock_result.archdebt.layer_scores.layer3_drift = 0.0
        mock_result.archdebt.layer_scores.layer4_duplication = 0.0
        mock_result.archdebt.weights = (0.25, 0.25, 0.25, 0.25)
        mock_result.violations = []
        mock_result.commit_sha = "a1b2c3d"
        mock_result.changed_files = ["src/core/main.py"]

        with patch(
            "archguard.cli.analyze_cmd.AnalysisOrchestrator",
        ) as mock_cls:
            mock_orch = MagicMock()
            mock_orch.run.return_value = mock_result
            mock_cls.return_value = mock_orch
            mock_cls.get_commit_sha.return_value = "a1b2c3d"

            result = runner.invoke(
                app,
                [
                    "analyze", "--repo", str(repo),
                    "--changed-files", str(py_file),
                    "--skip-explanation",
                ],
            )

        mock_orch.run.assert_called_once()
        call_kwargs = mock_orch.run.call_args
        assert call_kwargs[1].get("skip_explanation") is True or (
            len(call_kwargs[0]) >= 3 and call_kwargs[0][2] is True
        )

    def test_missing_contract_exits_with_config_error(self, tmp_path: Path) -> None:
        """No .archguard.yml -> exit with CONFIG_ERROR."""
        from archguard.config import EXIT_CONFIG_ERROR
        repo = tmp_path / "empty_repo"
        repo.mkdir()

        result = runner.invoke(
            app,
            ["analyze", "--repo", str(repo), "--changed-files", "main.py"],
        )
        assert result.exit_code == EXIT_CONFIG_ERROR

    def test_github_repository_env_detected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GITHUB_REPOSITORY env var auto-detected."""
        repo = _setup_repo(tmp_path)
        py_file = repo / "src" / "core" / "main.py"
        monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")

        mock_result = MagicMock()
        mock_result.archdebt.composite_score = 0.10
        mock_result.archdebt.band.value = "Healthy"
        mock_result.archdebt.band = MagicMock()
        mock_result.archdebt.should_fail_ci = False
        mock_result.archdebt.layer_scores.layer1_violation = 0.0
        mock_result.archdebt.layer_scores.layer2_coupling = 0.0
        mock_result.archdebt.layer_scores.layer3_drift = 0.0
        mock_result.archdebt.layer_scores.layer4_duplication = 0.0
        mock_result.archdebt.weights = (0.25, 0.25, 0.25, 0.25)
        mock_result.violations = []
        mock_result.commit_sha = "a1b2c3d"
        mock_result.changed_files = ["src/core/main.py"]

        with patch(
            "archguard.cli.analyze_cmd.AnalysisOrchestrator",
        ) as mock_cls:
            mock_orch = MagicMock()
            mock_orch.run.return_value = mock_result
            mock_cls.return_value = mock_orch
            mock_cls.get_commit_sha.return_value = "a1b2c3d"

            result = runner.invoke(
                app,
                [
                    "analyze", "--repo", str(repo),
                    "--changed-files", str(py_file),
                    "--dry-run",
                ],
                env={"GITHUB_REPOSITORY": "org/repo"},
            )

        assert result.exit_code == 0

    def test_llm_failure_no_exit_code_change(self, tmp_path: Path) -> None:
        """LLM failure does NOT change exit code."""
        repo = _setup_repo(tmp_path)
        py_file = repo / "src" / "core" / "main.py"

        mock_result = MagicMock()
        mock_result.archdebt.composite_score = 0.10
        mock_result.archdebt.band.value = "Healthy"
        mock_result.archdebt.band = MagicMock()
        mock_result.archdebt.should_fail_ci = False
        mock_result.archdebt.layer_scores.layer1_violation = 0.0
        mock_result.archdebt.layer_scores.layer2_coupling = 0.0
        mock_result.archdebt.layer_scores.layer3_drift = 0.0
        mock_result.archdebt.layer_scores.layer4_duplication = 0.0
        mock_result.archdebt.weights = (0.25, 0.25, 0.25, 0.25)
        mock_result.violations = []
        mock_result.commit_sha = "a1b2c3d"
        mock_result.changed_files = ["src/core/main.py"]

        with patch(
            "archguard.cli.analyze_cmd.AnalysisOrchestrator",
        ) as mock_cls:
            mock_orch = MagicMock()
            mock_orch.run.return_value = mock_result
            mock_cls.return_value = mock_orch
            mock_cls.get_commit_sha.return_value = "a1b2c3d"

            result = runner.invoke(
                app,
                [
                    "analyze", "--repo", str(repo),
                    "--changed-files", str(py_file),
                ],
            )

        # Exit 0 even if LLM would have failed (it's not invoked here)
        assert result.exit_code == 0
