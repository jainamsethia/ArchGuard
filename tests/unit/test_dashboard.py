import os
import pytest
from fastapi.testclient import TestClient
from archguard.dashboard.app import app
from unittest.mock import patch, MagicMock

# Create a TestClient for our app
client = TestClient(app)


@pytest.fixture
def mock_audit_logger():
    # Mock AuditLogger so the endpoint actually works without needing a real DB/file
    with patch("archguard.dashboard.app.AuditLogger") as mock_logger_cls:
        mock_instance = MagicMock()
        mock_instance.read_last_n_runs.return_value = []
        mock_logger_cls.return_value = mock_instance
        yield mock_instance


def test_api_runs_no_token_configured(mock_audit_logger, monkeypatch):
    """Test that /api/runs returns 200 when no token is configured."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    
    # We also mock the host to simulate it being localhost
    response = client.get("/api/runs")
    assert response.status_code == 200
    assert "runs" in response.json()


def test_api_runs_token_configured_no_auth(mock_audit_logger, monkeypatch):
    """Test that /api/runs returns 401 when ARCHGUARD_DASHBOARD_TOKEN is set and no auth header is provided."""
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "secret-token")
    
    response = client.get("/api/runs")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing token"


def test_api_runs_token_configured_with_correct_auth(mock_audit_logger, monkeypatch):
    """Test that /api/runs returns 200 with the correct bearer token."""
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "secret-token")
    
    response = client.get(
        "/api/runs",
        headers={"Authorization": "Bearer secret-token"}
    )
    assert response.status_code == 200
    assert "runs" in response.json()


def test_api_runs_token_configured_with_incorrect_auth(mock_audit_logger, monkeypatch):
    """Test that /api/runs returns 401 with an incorrect bearer token."""
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "secret-token")
    
    response = client.get(
        "/api/runs",
        headers={"Authorization": "Bearer wrong-token"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing token"
