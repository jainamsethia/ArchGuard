"""Unit tests for archguard init command."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from typer.testing import CliRunner

from archguard.cli.main import app
from archguard.cli.init_cmd import (
    latest_completed_phase,
    save_checkpoint,
)

runner: CliRunner = CliRunner()


def _create_py_files(tmp_path: Path) -> None:
    """Create 3 simple Python files for testing."""
    (tmp_path / "file1.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "file2.py").write_text("y = 2\n", encoding="utf-8")
    (tmp_path / "file3.py").write_text("z = 3\n", encoding="utf-8")


def _make_mock_deps(
    commit_count: int = 1,
    filenames: list[str] | None = None,
) -> dict[str, MagicMock]:
    """Create mocks for pydriller and sentence_transformers."""
    if filenames is None:
        filenames = ["file1.py", "file2.py", "file3.py"]

    # PyDriller mock
    mock_pydriller = MagicMock()
    mock_commits = []
    for _ in range(commit_count):
        mock_commit = MagicMock()
        mock_commit.hash = "deadbeef12345678abcdef0123456789abcdef00"
        mock_mfs = []
        for fn in filenames:
            mf = MagicMock()
            mf.filename = fn
            mock_mfs.append(mf)
        mock_commit.modified_files = mock_mfs
        mock_commits.append(mock_commit)

    mock_pydriller.Repository.return_value.traverse_commits.return_value = mock_commits

    # sentence-transformers mock
    mock_st = MagicMock()
    mock_model = MagicMock()
    mock_model.encode.side_effect = lambda texts, **kwargs: np.random.rand(
        len(texts),
        384,
    ).astype(np.float32)
    mock_st.SentenceTransformer.return_value = mock_model

    return {"pydriller": mock_pydriller, "sentence_transformers": mock_st}


class TestInitCommand:
    """Tests for archguard init."""

    @pytest.fixture(autouse=True)
    def mock_prompt(self):
        with patch("archguard.cli.init_cmd.Prompt.ask", return_value="1"):
            yield

    def test_empty_dir_exit_1(self, tmp_path: Path) -> None:
        """--confirm-all on empty dir -> exit 1 (no Python files)."""
        mocks = _make_mock_deps()
        with patch.dict(sys.modules, mocks):
            result = runner.invoke(
                app,
                ["init", "--confirm-all", "--repo", str(tmp_path)],
            )
        assert result.exit_code == 1

    def test_confirm_all_creates_contract(
        self,
        tmp_path: Path,
    ) -> None:
        """--confirm-all on dir with 3 .py files -> exit 0, contract created."""
        _create_py_files(tmp_path)
        mocks = _make_mock_deps()
        with patch.dict(sys.modules, mocks):
            result = runner.invoke(
                app,
                ["init", "--confirm-all", "--repo", str(tmp_path)],
            )
        if result.exit_code != 0:
            print("OUTPUT:", result.output)
            if result.exception:
                print("EXCEPTION:", repr(result.exception))
        assert result.exit_code == 0
        assert (tmp_path / ".archguard.yml").exists()

    def test_summary_has_all_sections(
        self,
        tmp_path: Path,
    ) -> None:
        """--confirm-all -> summary markdown has all 6 sections."""
        _create_py_files(tmp_path)
        mocks = _make_mock_deps()
        with patch.dict(sys.modules, mocks):
            runner.invoke(
                app,
                ["init", "--confirm-all", "--repo", str(tmp_path)],
            )
        summary = (tmp_path / ".archguard-init-summary.md").read_text(
            encoding="utf-8",
        )
        assert "## 1. Repository Overview" in summary
        assert "## 2. Communities Detected" in summary
        assert "## 3. Embedding Model" in summary
        assert "## 4. Coherence Warnings" in summary
        assert "## 5. Contract Written" in summary
        assert "## 6. Next Steps" in summary

    def test_shallow_clone_exit_1(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GITHUB_ACTIONS=true + commit_count < 100 -> exit 1."""
        _create_py_files(tmp_path)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        mocks = _make_mock_deps(commit_count=50)
        with patch.dict(sys.modules, mocks):
            result = runner.invoke(
                app,
                ["init", "--confirm-all", "--repo", str(tmp_path)],
            )
        assert result.exit_code == 1

    def test_force_ci_bypass(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GITHUB_ACTIONS=true + --force-ci -> proceeds normally."""
        _create_py_files(tmp_path)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        mocks = _make_mock_deps()
        with patch.dict(sys.modules, mocks):
            result = runner.invoke(
                app,
                ["init", "--confirm-all", "--force-ci", "--repo", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_resume_no_checkpoints(self, tmp_path: Path) -> None:
        """--resume with no checkpoints -> starts from phase 1."""
        assert latest_completed_phase(tmp_path) == 0
        # With no py files it will exit 1 at phase 1
        mocks = _make_mock_deps()
        with patch.dict(sys.modules, mocks):
            result = runner.invoke(
                app,
                ["init", "--confirm-all", "--resume", "--repo", str(tmp_path)],
            )
        assert result.exit_code == 1  # no py files

    def test_resume_skips_completed_phases(
        self,
        tmp_path: Path,
    ) -> None:
        """--resume with phase 2 checkpoint -> skips phases 1+2."""
        import networkx as nx

        _create_py_files(tmp_path)

        # Create phase 1 checkpoint
        save_checkpoint(
            tmp_path,
            1,
            {
                "total_files": 3,
                "total_loc": 3,
                "python_files": ["file1.py", "file2.py", "file3.py"],
            },
        )

        # Create phase 2 checkpoint with a graph
        g = nx.Graph()
        g.add_edge("file1.py", "file2.py", weight=1)
        g.add_edge("file2.py", "file3.py", weight=1)
        g.add_edge("file1.py", "file3.py", weight=1)
        save_checkpoint(
            tmp_path,
            2,
            {
                "commit_count": 1,
                "graph_nodes": 3,
                "graph_edges": 3,
                "graph_data": nx.node_link_data(g),
            },
        )

        mocks = _make_mock_deps()
        with patch.dict(sys.modules, mocks):
            result = runner.invoke(
                app,
                ["init", "--confirm-all", "--resume", "--repo", str(tmp_path)],
            )
        assert result.exit_code == 0
        assert (tmp_path / ".archguard.yml").exists()

    def test_generated_yaml_validates(self, tmp_path: Path) -> None:
        """Generated YAML passes validate_contract()."""
        import yaml
        from archguard.contract.validator import validate_contract

        _create_py_files(tmp_path)
        mocks = _make_mock_deps()
        with patch.dict(sys.modules, mocks):
            runner.invoke(
                app,
                ["init", "--confirm-all", "--repo", str(tmp_path)],
            )

        contract_path = tmp_path / ".archguard.yml"
        assert contract_path.exists()
        with contract_path.open(encoding="utf-8") as f:
            contract = yaml.safe_load(f)

        # Should not raise
        validate_contract(contract)

    def test_non_tty_auto_confirm(
        self,
        tmp_path: Path,
    ) -> None:
        """Non-TTY (is_tty() -> False) -> runs with confirm-all behavior."""
        _create_py_files(tmp_path)
        mocks = _make_mock_deps()
        with (
            patch("archguard.cli.init_cmd.is_tty", return_value=False),
            patch.dict(sys.modules, mocks),
        ):
            result = runner.invoke(
                app,
                ["init", "--repo", str(tmp_path)],
            )
        assert result.exit_code == 0
        assert (tmp_path / ".archguard.yml").exists()

    def test_phase4_embeddings_no_llm(self, tmp_path: Path) -> None:
        """_phase4_embeddings returns mock data when no_llm is True."""
        from archguard.cli.init_cmd import _phase4_embeddings

        communities = {"mod1": ["file1.py"]}
        res = _phase4_embeddings(communities, tmp_path, ["file1.py"], no_llm=True)
        assert res["modules_embedded"] == 1
        assert res["model_name"] == "none"

    def test_phase4_embeddings_no_nameerror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_phase4_embeddings must not raise NameError."""
        from archguard.cli.init_cmd import _phase4_embeddings

        _create_py_files(tmp_path)
        communities = {"mod1": ["file1.py", "file2.py", "file3.py"]}
        python_files = ["file1.py", "file2.py", "file3.py"]

        mocks = _make_mock_deps()
        with patch.dict(sys.modules, mocks):
            monkeypatch.setattr("archguard.cli.init_cmd._console", MagicMock())
            monkeypatch.setattr(
                "archguard.cli.init_cmd.Prompt.ask", lambda *args, **kwargs: "1"
            )

            res = _phase4_embeddings(communities, tmp_path, python_files, no_llm=False)

        assert res["modules_embedded"] == 1
        assert res["total_functions_embedded"] == 3

    def test_interactive_review_accept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_interactive_review handles 'y'."""
        from archguard.cli.init_cmd import _interactive_review

        communities = {"mod1": ["file1.py"]}
        monkeypatch.setattr("typer.prompt", lambda *a, **k: "y")
        res = _interactive_review(communities)
        assert "mod1" in res

    def test_interactive_review_skip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_interactive_review handles 'n'."""
        from archguard.cli.init_cmd import _interactive_review

        communities = {"mod1": ["file1.py"]}
        monkeypatch.setattr("typer.prompt", lambda *a, **k: "n")
        res = _interactive_review(communities)
        assert "mod1" not in res

    def test_interactive_review_rename(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_interactive_review handles 'rename'."""
        from archguard.cli.init_cmd import _interactive_review

        communities = {"mod1": ["file1.py"]}
        prompts = iter(["rename", "mod2"])
        monkeypatch.setattr("typer.prompt", lambda *a, **k: next(prompts))
        res = _interactive_review(communities)
        assert "mod2" in res
        assert "mod1" not in res

    def test_init_fallback_on_empty_repo(self, tmp_path: Path) -> None:
        """Test fallback to directory modules when commit history is sparse."""
        import subprocess
        import yaml

        # Create a git repo with 1 commit
        subprocess.run(["git", "init", str(tmp_path)])
        (tmp_path / "main.py").write_text("def hello(): pass")
        (tmp_path / "utils").mkdir()
        (tmp_path / "utils" / "helper.py").write_text("def help(): pass")
        subprocess.run(["git", "add", "."], cwd=tmp_path)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path)

        mocks = _make_mock_deps()
        with patch.dict(sys.modules, mocks):
            result = runner.invoke(
                app, ["init", "--repo", str(tmp_path), "--confirm-all"]
            )

        assert result.exit_code == 0
        contract_path = tmp_path / ".archguard.yml"
        assert contract_path.exists()

        with contract_path.open(encoding="utf-8") as f:
            contract = yaml.safe_load(f)

        assert len(contract["modules"]) >= 1  # At least one module detected
