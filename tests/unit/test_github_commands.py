"""Unit tests for GitHub slash commands and github-sync."""

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

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
def test_github_sync_suppress(mock_execute_suppress, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """github-sync dispatches suppress command based on issue_comment payload."""
    payload = {
        "action": "created",
        "issue": {"number": 42, "pull_request": {}},
        "comment": {
            "id": 999,
            "body": "/archguard suppress bad import",
            "user": {"login": "testuser"}
        },
        "repository": {"full_name": "org/repo"}
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
def test_github_sync_re_analyze(mock_execute_re_analyze, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """github-sync dispatches re-analyze command based on issue_comment payload."""
    payload = {
        "action": "created",
        "issue": {"number": 43, "pull_request": {}},
        "comment": {
            "id": 1000,
            "body": "/archguard re-analyze",
            "user": {"login": "testuser2"}
        },
        "repository": {"full_name": "org/repo2"}
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
