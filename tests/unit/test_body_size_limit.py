import pytest
from httpx import ASGITransport, AsyncClient

from archguard.dashboard.app import app


@pytest.mark.asyncio
async def test_body_too_large_returns_413():
    """Regression for MED-03: payloads over 1 MB are rejected before parsing."""
    # Arrange
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        oversized_body = b"x" * (1 * 1024 * 1024 + 1)
        # Act
        resp = await client.post(
            "/api/remediation/plan",
            content=oversized_body,
            headers={"content-length": str(len(oversized_body)), "content-type": "application/json"},
        )
    # Assert
    assert resp.status_code == 413
    assert "too large" in resp.json()["error"].lower()


@pytest.mark.asyncio
async def test_body_within_limit_passes_middleware():
    """Regression for MED-03: a 100-byte payload must not be rejected by the size middleware."""
    # Arrange
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        small_body = b'{"violations": []}'
        # Act — 422 from Pydantic is fine; we just need NOT 413
        resp = await client.post(
            "/api/remediation/plan",
            content=small_body,
            headers={"content-length": str(len(small_body)), "content-type": "application/json"},
        )
    # Assert
    assert resp.status_code != 413
