import pytest
from httpx import ASGITransport, AsyncClient

from archguard.dashboard.app import app


@pytest.mark.asyncio
async def test_path_traversal_job_id_rejected():
    """Regression for HIGH-02: job_id with path traversal characters returns 422."""
    # Arrange
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Act
        resp = await client.get("/api/v1/runs", params={"job_id": "../../etc/passwd"})
    # Assert
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"


@pytest.mark.asyncio
async def test_valid_uuid_job_id_accepted():
    """Regression for HIGH-02: a UUID-format job_id passes validation (may 404 if no data)."""
    # Arrange
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        valid_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        # Act
        resp = await client.get("/api/v1/runs", params={"job_id": valid_uuid})
    # Assert — 422 means the pattern rejected a valid UUID (regression)
    assert resp.status_code != 422, "Valid UUID was wrongly rejected with 422"
