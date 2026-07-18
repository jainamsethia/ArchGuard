from fastapi.testclient import TestClient
from archguard.dashboard.app import app

def test_get_deps_no_job_id_returns_400(monkeypatch):
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "test-token")
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", "1")
    client = TestClient(app, raise_server_exceptions=False)
    
    resp = client.get(
        "/api/v1/deps",
        headers={"Authorization": "Bearer test-token"}
    )
    
    assert resp.status_code == 400
    assert "No analysis selected" in resp.json()["detail"]

def test_get_deps_with_valid_job_id_and_missing_workspace(monkeypatch):
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "test-token")
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", "1")
    client = TestClient(app, raise_server_exceptions=False)
    
    resp = client.get(
        "/api/v1/deps?job_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        headers={"Authorization": "Bearer test-token"}
    )
    
    assert resp.status_code == 410
    assert "Analysis workspace no longer available" in resp.json()["detail"]
