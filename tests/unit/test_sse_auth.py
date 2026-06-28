import pytest
import os
import httpx
from archguard.dashboard.app import app
from archguard.dashboard._cookie_auth import issue_session


@pytest.mark.asyncio
async def test_sse_stream_returns_401_without_auth_when_token_set():
    """Regression for CRIT-02: SSE stream returns 401 when token configured and no cookie sent."""
    # Arrange
    os.environ["ARCHGUARD_DASHBOARD_TOKEN"] = "sse-test-token"
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Act — no cookie, no ?token param
            resp = await client.get("/api/jobs/a1b2c3d4-e5f6-7890-abcd-ef1234567890/stream")
        # Assert
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"
    finally:
        del os.environ["ARCHGUARD_DASHBOARD_TOKEN"]


@pytest.mark.asyncio
async def test_sse_stream_accepts_valid_session_cookie():
    """Regression for CRIT-02: SSE stream must not return 401 with a valid session cookie."""
    # Arrange
    token = "sse-cookie-test"
    os.environ["ARCHGUARD_DASHBOARD_TOKEN"] = token
    cookie_value = issue_session(token)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Act
            resp = await client.get(
                "/api/jobs/a1b2c3d4-e5f6-7890-abcd-ef1234567890/stream",
                cookies={"archguard_session": cookie_value},
            )
        # Assert — 404 or streaming is fine; 401 is the regression
        assert resp.status_code != 401, f"Valid cookie was rejected with 401"
    finally:
        del os.environ["ARCHGUARD_DASHBOARD_TOKEN"]


@pytest.mark.asyncio
async def test_sse_stream_accepts_query_param_token():
    """Regression for CRIT-02: SSE stream must not return 401 with correct ?token= param."""
    # Arrange
    os.environ["ARCHGUARD_DASHBOARD_TOKEN"] = "sse-qs-token"
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Act
            resp = await client.get(
                "/api/jobs/a1b2c3d4-e5f6-7890-abcd-ef1234567890/stream",
                params={"token": "sse-qs-token"},
            )
        # Assert — 404 is fine; 401 is the regression
        assert resp.status_code != 401, f"?token= param was rejected with 401"
    finally:
        del os.environ["ARCHGUARD_DASHBOARD_TOKEN"]
