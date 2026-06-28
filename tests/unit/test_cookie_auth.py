import pytest
import os
import httpx
from archguard.dashboard._cookie_auth import (
    issue_session, validate_session_cookie, revoke_session, _SESSIONS
)


def test_issue_and_validate_session():
    """Regression for CRIT-01: a freshly issued session cookie must validate successfully."""
    # Arrange
    token = "test-secret-token-abc123"
    _SESSIONS.clear()
    # Act
    cookie_value = issue_session(token)
    result = validate_session_cookie(cookie_value, token)
    # Assert
    assert result is True, "Freshly issued session should be valid"


def test_wrong_token_rejected():
    """Regression for CRIT-01: a session issued with one token must not validate with another."""
    # Arrange
    _SESSIONS.clear()
    cookie_value = issue_session("correct-token")
    # Act
    result = validate_session_cookie(cookie_value, "wrong-token")
    # Assert
    assert result is False, "Session with wrong token must be rejected"


def test_revoked_session_rejected():
    """Regression for CRIT-01: after revocation, the same cookie must fail validation."""
    # Arrange
    token = "revoke-test-token"
    _SESSIONS.clear()
    cookie_value = issue_session(token)
    assert validate_session_cookie(cookie_value, token) is True
    # Act
    revoke_session(cookie_value)
    result = validate_session_cookie(cookie_value, token)
    # Assert
    assert result is False, "Revoked session must be invalid"


def test_malformed_cookie_rejected():
    """Regression for CRIT-01: a cookie with no dot separator must be rejected gracefully."""
    result = validate_session_cookie("no-dot-in-this-value", "any-token")
    assert result is False, "Malformed cookie must be rejected"


@pytest.mark.asyncio
async def test_login_endpoint_sets_cookie():
    """Regression for CRIT-01: POST /api/auth/login with correct token sets session cookie."""
    # Arrange
    os.environ["ARCHGUARD_DASHBOARD_TOKEN"] = "integration-test-token"
    try:
        transport = httpx.ASGITransport(app=__import__("archguard.dashboard.app", fromlist=["app"]).app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Act
            resp = await client.post("/api/auth/login", data={"token": "integration-test-token"})
        # Assert
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        assert "archguard_session" in resp.cookies, "Session cookie must be set on successful login"
    finally:
        del os.environ["ARCHGUARD_DASHBOARD_TOKEN"]


@pytest.mark.asyncio
async def test_login_endpoint_rejects_wrong_token():
    """Regression for CRIT-01: POST /api/auth/login with wrong token returns 401."""
    # Arrange
    os.environ["ARCHGUARD_DASHBOARD_TOKEN"] = "correct-token"
    try:
        transport = httpx.ASGITransport(app=__import__("archguard.dashboard.app", fromlist=["app"]).app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/auth/login", data={"token": "wrong-token"})
        assert resp.status_code == 401
    finally:
        del os.environ["ARCHGUARD_DASHBOARD_TOKEN"]


@pytest.mark.asyncio
async def test_bearer_token_still_works_after_cookie_auth_added():
    """Regression for CRIT-01: existing Bearer token auth must continue to pass check_token()."""
    # Arrange
    os.environ["ARCHGUARD_DASHBOARD_TOKEN"] = "bearer-test-token"
    try:
        from archguard.dashboard.app import app
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/runs",
                headers={"Authorization": "Bearer bearer-test-token"},
            )
        # Assert: 200 or any non-401 means Bearer auth worked
        assert resp.status_code != 401, f"Bearer auth returned 401 — regression"
    finally:
        del os.environ["ARCHGUARD_DASHBOARD_TOKEN"]
