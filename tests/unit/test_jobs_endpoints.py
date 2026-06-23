import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from archguard.dashboard.app import app

@pytest.fixture
def client():
    return TestClient(app)

def test_submit_job_invalid_url(client):
    """Invalid GitHub URL → 422."""
    resp = client.post("/api/jobs", json={"github_url": "not-a-url"})
    assert resp.status_code == 422

def test_submit_job_returns_202(client):
    """Valid URL → 202 with job_id."""
    # Patch run_job to be a no-op (don't actually clone anything)
    with patch("archguard.dashboard.job_manager.JobManager.run_job", return_value=None):
        resp = client.post(
            "/api/jobs",
            json={"github_url": "https://github.com/pallets/flask"},
        )
        
    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    assert body["status"] == "queued"
    assert "/api/jobs/" in body["poll_url"]
    assert "/stream" in body["stream_url"]

def test_get_job_not_found(client):
    """Unknown job_id → 404."""
    resp = client.get("/api/jobs/does-not-exist-12345")
    assert resp.status_code == 404

def test_get_job_status_queued(client):
    """Freshly created job shows queued status."""
    from archguard.dashboard.job_manager import AnalysisJob, JobStatus
    
    fake_job = AnalysisJob(
        id="test-uuid",
        github_url="https://github.com/pallets/flask",
        status=JobStatus.QUEUED,
    )
    
    with patch("archguard.dashboard.job_manager.job_manager.get_job", return_value=fake_job):
        resp = client.get("/api/jobs/test-uuid")
        
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"

def test_list_jobs_empty(client):
    """Empty job store returns empty list."""
    from archguard.dashboard.job_manager import job_manager
    job_manager._jobs.clear()
    
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    assert resp.json()["jobs"] == []
