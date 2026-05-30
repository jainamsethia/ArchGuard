"""Unit tests for archguard.github.client."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from archguard.github.client import _get_pr_number, post_comment, GitHubClient


def test_get_pr_number_from_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_get_pr_number parses PR number from GITHUB_EVENT_PATH payload successfully."""
    # Test case 1: pull_request object exists
    event_data = {"pull_request": {"number": 42}}
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event_data), encoding="utf-8")

    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
    assert _get_pr_number() == 42


def test_get_pr_number_fallback_from_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_get_pr_number parses number from top level of payload if pull_request doesn't exist."""
    event_data = {"number": 100}
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event_data), encoding="utf-8")

    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
    assert _get_pr_number() == 100


def test_get_pr_number_missing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_pr_number returns None if GITHUB_EVENT_PATH is absent."""
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert _get_pr_number() is None


def test_get_pr_number_invalid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_get_pr_number returns None on invalid JSON or file errors."""
    event_file = tmp_path / "event.json"
    event_file.write_text("invalid json", encoding="utf-8")

    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
    assert _get_pr_number() is None


def test_post_comment_no_pr_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """post_comment returns False if PR number cannot be determined."""
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    # Ensure post_comment returns False and doesn't raise
    assert post_comment("org/repo", "test body", pr_number=None) is False


@patch("archguard.github.client.requests.post")
@patch("archguard.github.client.requests.get")
def test_post_comment_method_success(
    mock_get: MagicMock,
    mock_post: MagicMock,
) -> None:
    """GitHubClient.post_comment succeeds when dependencies are correct."""
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "resources": {"core": {"remaining": 5000}}
    }

    mock_post.return_value.status_code = 201

    with patch.dict("os.environ", {"GITHUB_TOKEN": "test"}):
        client = GitHubClient()
        res = client.post_comment("org/repo", "test body", pr_number=55)
        assert res is True
        mock_post.assert_called_once()


@patch("archguard.github.client.GitHubClient")
def test_post_comment_function_success(
    mock_client_class: MagicMock,
) -> None:
    """post_comment top-level function delegates correctly to GitHubClient."""
    mock_client = mock_client_class.return_value
    mock_client.post_comment.return_value = True

    res = post_comment("org/repo", "test body", pr_number=55, token="test_token")
    assert res is True
    mock_client_class.assert_called_once_with(token="test_token")
    mock_client.post_comment.assert_called_once_with(
        "org/repo", "test body", pr_number=55
    )
