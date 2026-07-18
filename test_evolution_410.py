from fastapi.testclient import TestClient
from archguard.dashboard.app import app
import pytest

def test_evolution_410_when_workspace_gone(monkeypatch):
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "test-token")
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", "1")
    client = TestClient(app, raise_server_exceptions=False)
    
    # job_id provided, but no workspace exists, so get_target_path returns Path.cwd()
    # It should raise 410
    resp = client.post(
        "/api/v1/evolution/analyze?job_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        json={"max_commits": 5},
        headers={"Authorization": "Bearer test-token"}
    )
    
    assert resp.status_code == 410
    assert "Analysis workspace no longer available" in resp.json()["detail"]
