import asyncio
import time
import httpx
from fastapi.testclient import TestClient
from unittest.mock import patch
from archguard.dashboard.app import app
import pytest

@pytest.mark.asyncio
async def test_concurrent_validate_and_health():
    # We will use an ASGITransport to simulate concurrent requests in the same event loop.
    transport = httpx.ASGITransport(app=app)
    
    # We mock fetch_repo_metadata_public to simulate a slow network call (e.g. 2 seconds)
    def slow_mock(*args, **kwargs):
        time.sleep(2.0)
        return {
            "name": "test-repo",
            "full_name": "owner/test-repo",
            "description": "desc",
            "language": "Python",
            "stargazers_count": 0,
            "default_branch": "main",
            "size": 100,
            "private": False,
            "clone_url": "https://github.com/owner/test-repo.git"
        }
        
    with patch("archguard.dashboard.routes.jobs.fetch_repo_metadata_public", side_effect=slow_mock):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            
            # Start the slow request
            task_validate = asyncio.create_task(
                client.post("/api/v1/jobs/validate", json={"github_url": "https://github.com/owner/test-repo"})
            )
            
            # Give it a tiny bit of time to start
            await asyncio.sleep(0.1)
            
            # Start the fast request
            start_time = time.time()
            resp_health = await client.get("/health")
            elapsed = time.time() - start_time
            
            assert resp_health.status_code == 200
            # It should complete almost instantly, not wait for the 2.0s sleep!
            assert elapsed < 0.5, f"Health endpoint blocked! Took {elapsed:.2f} seconds"
            
            # Wait for validate to finish
            resp_validate = await task_validate
            assert resp_validate.status_code == 200
