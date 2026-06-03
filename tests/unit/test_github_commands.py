"""Unit tests for GitHub slash commands and github-sync."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from archguard.cli.main import app
from archguard.github.commands import parse_commands, ArchGuardCommand

runner = CliRunner()


def test_parse_commands_suppress_and_reanalyze() -> None:
    """Test parsing of slash commands with arguments."""
    body = (
        "Some text before\n"
        "/archguard suppress Imports `utils.foo` (disallowed)\n"
        "/archguard re-analyze\n"
        "/archguard unknown-cmd\n"
    )
    cmds = parse_commands(body, comment_id=123, author="user1")

    assert len(cmds) == 2
    assert cmds[0].command == ArchGuardCommand.SUPPRESS
    assert cmds[0].args == ["Imports", "`utils.foo`", "(disallowed)"]
    assert cmds[0].comment_id == 123
    assert cmds[0].author == "user1"

    assert cmds[1].command == ArchGuardCommand.RE_ANALYZE
    assert cmds[1].args == []


@patch("archguard.cli.github_sync_cmd._execute_suppress")
def test_github_sync_suppress(
    mock_execute_suppress, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """github-sync dispatches suppress command based on issue_comment payload."""
    payload = {
        "action": "created",
        "issue": {"number": 42, "pull_request": {}},
        "comment": {
            "id": 999,
            "body": "/archguard suppress bad import",
            "user": {"login": "testuser"},
        },
        "repository": {"full_name": "org/repo"},
    }

    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))

    repo = tmp_path / "repo"
    repo.mkdir()

    result = runner.invoke(app, ["github-sync", "--repo", str(repo)])

    assert result.exit_code == 0
    mock_execute_suppress.assert_called_once()

    called_cmd = mock_execute_suppress.call_args[0][0]
    assert called_cmd.command == ArchGuardCommand.SUPPRESS
    assert called_cmd.args == ["bad", "import"]
    assert called_cmd.author == "testuser"

    assert mock_execute_suppress.call_args[0][2] == "org/repo"
    assert mock_execute_suppress.call_args[0][3] == 42


@patch("archguard.cli.github_sync_cmd._execute_re_analyze")
def test_github_sync_re_analyze(
    mock_execute_re_analyze, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """github-sync dispatches re-analyze command based on issue_comment payload."""
    payload = {
        "action": "created",
        "issue": {"number": 43, "pull_request": {}},
        "comment": {
            "id": 1000,
            "body": "/archguard re-analyze",
            "user": {"login": "testuser2"},
        },
        "repository": {"full_name": "org/repo2"},
    }

    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))

    repo = tmp_path / "repo"
    repo.mkdir()

    result = runner.invoke(app, ["github-sync", "--repo", str(repo)])

    assert result.exit_code == 0
    mock_execute_re_analyze.assert_called_once()

    assert mock_execute_re_analyze.call_args[0][1] == "org/repo2"
    assert mock_execute_re_analyze.call_args[0][2] == 43

@patch("archguard.analysis.layers.AnalysisOrchestrator")
@patch("archguard.cli._analyze_core._resolve_changed_files")
@patch("archguard.github.client.GitHubClient")
@patch("archguard.github.comments.PRCommentManager")
def test_execute_re_analyze_direct_call(
    mock_pr_mgr, mock_gh_client, mock_resolve, mock_orchestrator, tmp_path: Path
) -> None:
    """_execute_re_analyze calls AnalysisOrchestrator directly without a Typer context."""
    from archguard.cli.github_sync_cmd import _execute_re_analyze
    
    mock_instance = mock_orchestrator.return_value.__enter__.return_value
    mock_instance.run.return_value = "fake_result"
    
    mock_resolve.return_value = []
    mock_orchestrator.get_commit_sha.return_value = "fake_sha"
    
    # Should complete without error when ctx=None
    _execute_re_analyze(tmp_path, "org/repo", 42, None)
    
    # Verify orchestrator was instantiated and run was called
    mock_orchestrator.assert_called_once_with(tmp_path)
    mock_instance.run.assert_called_once_with(changed_files=[], commit_sha="fake_sha", quiet=True)

@patch("archguard.suppression.store.SuppressionStore")
@patch("archguard.github.client.post_comment")
def test_execute_suppress_success(mock_post, mock_store_cls, tmp_path: Path) -> None:
    from archguard.cli.github_sync_cmd import _execute_suppress
    from archguard.github.commands import SlashCommand
    mock_store = mock_store_cls.return_value
    cmd = SlashCommand(command=ArchGuardCommand.SUPPRESS, args=["api", "1", "Imports", "from", "db"], author="testuser", comment_id=1)
    
    _execute_suppress(cmd, tmp_path, "org/repo", 42)
    
    mock_store.add.assert_called_once_with(
        module="api",
        layer=1,
        message="Imports from db",
        reason="Suppressed via PR comment by @testuser",
        pr_number=42,
    )
    mock_post.assert_called_once()
    assert "✅ @testuser Suppressed `api` L1: `Imports from db`" in mock_post.call_args[0][1]

@patch("archguard.suppression.store.SuppressionStore")
@patch("archguard.github.client.post_comment")
def test_execute_suppress_usage_error(mock_post, mock_store_cls, tmp_path: Path) -> None:
    from archguard.cli.github_sync_cmd import _execute_suppress
    from archguard.github.commands import SlashCommand
    mock_store = mock_store_cls.return_value
    cmd = SlashCommand(command=ArchGuardCommand.SUPPRESS, args=["api", "1"], author="testuser", comment_id=1)
    
    _execute_suppress(cmd, tmp_path, "org/repo", 42)
    
    mock_store.add.assert_not_called()
    mock_post.assert_called_once()
    assert "Usage:" in mock_post.call_args[0][1]

@patch("archguard.suppression.store.SuppressionStore")
@patch("archguard.github.client.post_comment")
def test_execute_suppress_invalid_layer(mock_post, mock_store_cls, tmp_path: Path) -> None:
    from archguard.cli.github_sync_cmd import _execute_suppress
    from archguard.github.commands import SlashCommand
    mock_store = mock_store_cls.return_value
    cmd = SlashCommand(command=ArchGuardCommand.SUPPRESS, args=["api", "invalid", "msg"], author="testuser", comment_id=1)
    
    _execute_suppress(cmd, tmp_path, "org/repo", 42)
    
    mock_store.add.assert_not_called()
    mock_post.assert_called_once()
    assert "Layer must be an integer" in mock_post.call_args[0][1]
