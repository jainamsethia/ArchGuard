import pytest
from httpx import ASGITransport, AsyncClient

from archguard.dashboard.app import app


@pytest.mark.asyncio
async def test_body_too_large_chunked_returns_413():
    """Verify that chunked requests with missing Content-Length are capped at read-time."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create an async generator yielding chunked data > 1 MB
        async def generate_chunks():
            chunk = b"x" * 1024 * 100  # 100 KB chunks
            for _ in range(12):  # 12 chunks = 1.2 MB
                yield chunk

        # httpx will automatically stream and omit Content-Length
        resp = await client.post(
            "/api/v1/remediation/plan",
            content=generate_chunks(),
            headers={"content-type": "application/json"},
        )

    assert resp.status_code == 413
    assert "too large" in resp.json()["error"].lower()
