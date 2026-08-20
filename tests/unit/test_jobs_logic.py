from unittest.mock import MagicMock, patch

import httpx
import pytest

from archguard.dashboard.routes.jobs import (
    GitHubRateLimitError,
    build_safe_clone_url,
    fetch_repo_metadata_public,
    parse_github_url,
)


def test_parse_github_url() -> None:
    assert parse_github_url("https://github.com/foo/bar.git") == ("foo", "bar")
    assert parse_github_url("https://github.com/foo/bar") == ("foo", "bar")
    assert parse_github_url("git@github.com:foo/bar.git") == ("foo", "bar")

    with pytest.raises(ValueError):
        parse_github_url("https://github.com/foo")
    with pytest.raises(ValueError):
        parse_github_url("https://github.com/foo/bar/baz")
    with pytest.raises(ValueError):
        parse_github_url("invalid_url")

def test_build_safe_clone_url() -> None:
    assert build_safe_clone_url("foo", "bar") == "https://github.com/foo/bar.git"

@patch("httpx.Client.get")
def test_fetch_repo_metadata_public(mock_get: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"name": "bar", "full_name": "foo/bar"}
    mock_get.return_value = mock_resp

    data = fetch_repo_metadata_public("foo", "bar")
    assert data["name"] == "bar"

    mock_resp.status_code = 404
    with pytest.raises(ValueError, match="not found"):
        fetch_repo_metadata_public("foo", "bar")

    mock_resp.status_code = 403
    mock_resp.headers = {"X-RateLimit-Remaining": "0"}
    with pytest.raises(GitHubRateLimitError):
        fetch_repo_metadata_public("foo", "bar")

    mock_resp.status_code = 500
    with pytest.raises(RuntimeError):
        fetch_repo_metadata_public("foo", "bar")

    # Simulate RequestError
    mock_get.side_effect = httpx.RequestError("network error")
    with pytest.raises(RuntimeError):
        fetch_repo_metadata_public("foo", "bar")
