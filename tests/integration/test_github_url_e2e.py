import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from archguard.dashboard.app import app
from tests.db_fixtures import requires_postgres


@pytest.mark.integration
def test_validate_real_flask_repo():
    """Live call to GitHub API — requires network access."""
    client = TestClient(app)
    resp = client.post(
        "/api/jobs/validate",
        json={"github_url": "https://github.com/pallets/flask"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["owner"] == "pallets"
    assert data["stars"] > 0

@requires_postgres
@pytest.mark.integration
@pytest.mark.asyncio
async def test_submit_job_and_poll_to_complete(live_db):
    """Submit a job and poll until COMPLETE or FAILED (max 5 minutes)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        submit = await client.post(
            "/api/jobs",
            json={"github_url": "https://github.com/psf/requests"},
        )
        assert submit.status_code == 202
        job_id = submit.json()["job_id"]
        deadline = time.time() + 300  # 5 minute timeout

        status = None
        while time.time() < deadline:
            status_resp = await client.get(f"/api/jobs/{job_id}")
            status = status_resp.json()["status"]
            if status in ("complete", "failed"):
                break
            await asyncio.sleep(5)

        assert status in ("complete", "failed"), f"Job did not finish: {status}"

