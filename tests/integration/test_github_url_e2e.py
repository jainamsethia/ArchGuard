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
        "/api/v1/jobs/validate",
        json={"github_url": "https://github.com/pallets/flask"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["owner"] == "pallets"
    assert data["stars"] > 0

@requires_postgres
@pytest.mark.integration
@pytest.mark.asyncio
async def test_submit_job_and_poll_to_complete(live_db, monkeypatch):
    """Submit a job and poll until COMPLETE or FAILED (max 5 minutes).

    Forced inline. Submission hands the job to the queue now, so with REDIS_URL
    set and no worker running it would sit at "queued" until the deadline --
    which is correct behaviour and a useless test. The worker path itself is
    covered by test_worker_roundtrip.py against a real arq worker.
    """
    monkeypatch.setenv("ARCHGUARD_INLINE_ANALYSIS", "1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        submit = await client.post(
            "/api/v1/jobs",
            json={"github_url": "https://github.com/psf/requests"},
        )
        assert submit.status_code == 202
        job_id = submit.json()["job_id"]
        deadline = time.time() + 300  # 5 minute timeout

        status = None
        while time.time() < deadline:
            status_resp = await client.get(f"/api/v1/jobs/{job_id}")
            status = status_resp.json()["status"]
            if status in ("complete", "failed"):
                break
            await asyncio.sleep(5)

        assert status in ("complete", "failed"), f"Job did not finish: {status}"

