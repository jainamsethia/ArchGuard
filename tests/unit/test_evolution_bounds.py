import pytest
from httpx import AsyncClient, ASGITransport
from archguard.dashboard.app import app


@pytest.mark.asyncio
async def test_max_commits_over_limit_returns_422():
    """Regression for MED-02: max_commits above 100 must return HTTP 422."""
    # Arrange
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Act
        resp = await client.post(
            "/api/evolution/analyze",
            json={"max_commits": 100001},
            headers={"content-type": "application/json"},
        )
    # Assert
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"


@pytest.mark.asyncio
async def test_max_commits_at_limit_accepted():
    """Regression for MED-02: max_commits=100 must pass Pydantic validation."""
    # Arrange
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Act — may return 4xx for other reasons, but NOT 422 for max_commits
        resp = await client.post(
            "/api/evolution/analyze",
            json={"max_commits": 100},
            headers={"content-type": "application/json"},
        )
    # Assert
    assert resp.status_code != 422, "max_commits=100 was wrongly rejected"
