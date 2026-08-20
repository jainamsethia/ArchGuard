"""Unit tests for archguard.github.client."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from archguard.github.client import GitHubClient, _get_pr_number, post_comment


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


@patch("archguard.github.client.httpx.Client.post")
@patch("archguard.github.client.httpx.Client.get")
def test_post_comment_method_success(
    mock_get: MagicMock,
    mock_post: MagicMock,
) -> None:
    """GitHubClient.post_comment succeeds when dependencies are correct."""
    mock_get.return_value.status_code = 200
    mock_get.return_value.headers = {"X-OAuth-Scopes": "repo"}
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


@patch("archguard.github.client.httpx.Client.get")
def test_get_pr_retry_behavior(mock_get, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test get_pr retry behavior to ensure no nested decorators."""
    import httpx

    from archguard.github.client import GitHubClient

    # Mock httpx.Client.get to fail twice then succeed
    response_500 = MagicMock()
    response_500.status_code = 500
    http_error = httpx.HTTPStatusError(
        "500 Error", request=MagicMock(), response=response_500
    )
    http_error.status = 500  # Avoid AttributeError in retry.py
    http_error.response = response_500
    response_500.raise_for_status.side_effect = http_error

    response_200 = MagicMock()
    response_200.status_code = 200
    response_200.json.return_value = {"pr": "data"}

    mock_get.side_effect = [
        # Constructor
        MagicMock(status_code=200, headers={"X-OAuth-Scopes": "repo"}),
        # Attempt 1
        MagicMock(
            status_code=200, json=lambda: {"resources": {"core": {"remaining": 5000}}}
        ),
        response_500,
        # Attempt 2
        MagicMock(
            status_code=200, json=lambda: {"resources": {"core": {"remaining": 5000}}}
        ),
        response_500,
        # Attempt 3
        MagicMock(
            status_code=200, json=lambda: {"resources": {"core": {"remaining": 5000}}}
        ),
        response_200,
    ]

    with patch.dict("os.environ", {"GITHUB_TOKEN": "test"}):
        client = GitHubClient()
        # To speed up tests, mock time.sleep inside exponential_backoff
        with patch("archguard.utils.retry.time.sleep"):
            res = client.get_pr("org/repo", 1)

        assert res == {"pr": "data"}
        # 1 constructor call + 3 rate limit checks + 3 API calls = 7 calls to httpx.Client.get
        assert mock_get.call_count == 7


@patch("archguard.github.client.httpx.Client.get")
def test_get_pr_changed_files_pagination(mock_get: MagicMock) -> None:
    """Test get_pr_changed_files correctly paginates and combines results."""
    from archguard.github.client import GitHubClient

    # Mock responses for pages 1, 2, 3
    page1_data = [{"filename": f"file_page1_{i}.txt"} for i in range(300)]
    page2_data = [{"filename": f"file_page2_{i}.txt"} for i in range(150)]
    page3_data = []

    # Mock responses
    response_auth = MagicMock(status_code=200, headers={"X-OAuth-Scopes": "repo"})
    response_rate = MagicMock(status_code=200)
    response_rate.json.return_value = {"resources": {"core": {"remaining": 5000}}}

    response_p1 = MagicMock(status_code=200)
    response_p1.json.return_value = page1_data

    response_p2 = MagicMock(status_code=200)
    response_p2.json.return_value = page2_data

    response_p3 = MagicMock(status_code=200)
    response_p3.json.return_value = page3_data

    mock_get.side_effect = [
        response_auth,  # __init__ validation
        response_rate,  # _check_rate_limit before loop
        response_p1,  # page 1
        response_p2,  # page 2
        response_p3,  # page 3
    ]

    with patch.dict("os.environ", {"GITHUB_TOKEN": "test"}):
        client = GitHubClient()
        files = client.get_pr_changed_files("org/repo", 42)

        assert len(files) == 450
        assert mock_get.call_count == 5

        # Verify page numbers in URLs
        urls = [call.args[0] for call in mock_get.call_args_list[2:]]
        assert "page=1" in urls[0]
        assert "page=2" in urls[1]
        assert "page=3" in urls[2]


@patch("archguard.github.client.httpx.Client.get")
def test_check_rate_limit_raises_immediately(mock_get: MagicMock) -> None:
    from archguard.github.client import GitHubClient, RateLimitExceededException

    response_auth = MagicMock(status_code=200, headers={"X-OAuth-Scopes": "repo"})
    response_rate = MagicMock(status_code=200)
    response_rate.json.return_value = {
        "resources": {"core": {"remaining": 10, "reset": 9999999999}}
    }

    mock_get.side_effect = [response_auth, response_rate]

    with patch.dict("os.environ", {"GITHUB_TOKEN": "test"}):
        client = GitHubClient()
        with pytest.raises(RateLimitExceededException) as exc:
            client._check_rate_limit()

        assert "remaining" in str(exc.value)


@patch("archguard.github.client.httpx.Client.post")
@patch("archguard.github.client.httpx.Client.get")
def test_post_comment_catches_rate_limit(
    mock_get: MagicMock, mock_post: MagicMock
) -> None:
    from archguard.github.client import GitHubClient

    response_auth = MagicMock(status_code=200, headers={"X-OAuth-Scopes": "repo"})
    response_rate = MagicMock(status_code=200)
    response_rate.json.return_value = {
        "resources": {"core": {"remaining": 10, "reset": 9999999999}}
    }

    mock_get.side_effect = [response_auth, response_rate]

    with patch.dict("os.environ", {"GITHUB_TOKEN": "test"}):
        client = GitHubClient()
        result = client.post_comment("org/repo", "test body", pr_number=55)

        assert result is False
        mock_post.assert_not_called()
