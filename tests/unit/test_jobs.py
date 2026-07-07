import os
from fastapi.testclient import TestClient
from archguard.dashboard.app import app
import pytest

def test_get_job_status_bearer_auth(monkeypatch):
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "test-token-123")
    client = TestClient(app)
    response = client.get(
        "/api/jobs/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": "Bearer test-token-123"}
    )
    assert response.status_code == 404
