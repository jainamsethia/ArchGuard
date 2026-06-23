"""Tests for GitHub URL parsing and the /api/jobs/validate endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from archguard.dashboard.app import app
from archguard.dashboard.routes.jobs import parse_github_url


# --------------------------------------------------------------------------
# Fixture: FastAPI test client
# --------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Provide a TestClient with rate-limits cleared and no auth token."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._state import RATE_LIMITS

    RATE_LIMITS.clear()
    return TestClient(app)


# --------------------------------------------------------------------------
# parse_github_url tests
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/pallets/flask", ("pallets", "flask")),
        ("https://github.com/pallets/flask.git", ("pallets", "flask")),
        ("https://github.com/pallets/flask/tree/main", ("pallets", "flask")),
        (
            "https://github.com/pallets/flask/blob/main/README.md",
            ("pallets", "flask"),
        ),
        ("git@github.com:pallets/flask.git", ("pallets", "flask")),
        ("https://github.com/pallets/flask/", ("pallets", "flask")),
    ],
)
def test_parse_github_url_valid(url: str, expected: tuple[str, str]) -> None:
    assert parse_github_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "not-a-url",
        "https://gitlab.com/owner/repo",
        "https://github.com/onlyowner",
        "",
    ],
)
def test_parse_github_url_invalid(url: str) -> None:
    with pytest.raises(ValueError, match="Cannot parse GitHub URL"):
        parse_github_url(url)


# --------------------------------------------------------------------------
# validate endpoint tests
# --------------------------------------------------------------------------


def test_validate_endpoint_invalid_url(client: TestClient) -> None:
    """Malformed URL → 422."""
    resp = client.post("/api/jobs/validate", json={"github_url": "not-a-url"})
    assert resp.status_code == 422


def test_validate_endpoint_not_found(client: TestClient) -> None:
    """GitHub returns 404 → endpoint returns 404."""
    with patch(
        "archguard.dashboard.routes.jobs.fetch_repo_metadata_public",
        side_effect=ValueError("not found"),
    ):
        resp = client.post(
            "/api/jobs/validate",
            json={"github_url": "https://github.com/pallets/flask"},
        )
    assert resp.status_code == 404


def test_validate_endpoint_rate_limit(client: TestClient) -> None:
    """GitHub returns 403 → endpoint returns 404 with helpful message."""
    with patch(
        "archguard.dashboard.routes.jobs.fetch_repo_metadata_public",
        side_effect=ValueError("rate limit"),
    ):
        resp = client.post(
            "/api/jobs/validate",
            json={"github_url": "https://github.com/pallets/flask"},
        )
    assert resp.status_code == 404
    assert "rate limit" in resp.json()["detail"].lower()


def test_validate_endpoint_success(client: TestClient) -> None:
    """Valid URL + mocked 200 → 200 with RepoMetadata."""
    with patch(
        "archguard.dashboard.routes.jobs.fetch_repo_metadata_public",
        return_value={
            "name": "flask",
            "full_name": "pallets/flask",
            "description": "The Python micro framework.",
            "language": "Python",
            "stargazers_count": 60000,
            "default_branch": "main",
            "size": 5000,
            "private": False,
            "clone_url": "https://github.com/pallets/flask.git",
        },
    ):
        resp = client.post(
            "/api/jobs/validate",
            json={"github_url": "https://github.com/pallets/flask"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["owner"] == "pallets"
    assert body["repo"] == "flask"
    assert body["stars"] == 60000
