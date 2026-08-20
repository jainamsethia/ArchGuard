"""Regression test for CRIT-04: get_job_status must not crash when a token is set."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_token(monkeypatch):
    """TestClient with ARCHGUARD_DASHBOARD_TOKEN set."""
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "test-token-crit04")
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", "1")
    from archguard.dashboard.app import app
    return TestClient(app, raise_server_exceptions=False)


def test_get_job_status_with_token_no_cookie_returns_401_not_500(client_with_token):
    """Authenticated endpoint must return 401 (not 500 NameError) when no cookie."""
    response = client_with_token.get("/api/jobs/00000000-0000-0000-0000-000000000001")
    # Must not be 500; 401 is correct (no auth credentials provided)
    assert response.status_code == 401, (
        f"Expected 401, got {response.status_code}. "
        f"Response body: {response.text[:200]}"
    )
    assert "Internal server error" not in response.text
    assert "NameError" not in response.text


def test_get_job_status_with_valid_token_in_querystring_returns_404_not_500(client_with_token):
    """Valid Bearer + ?token= query param must produce 404 (job not found), not 500."""
    response = client_with_token.get(
        "/api/jobs/00000000-0000-0000-0000-000000000001?token=test-token-crit04",
        headers={"Authorization": "Bearer test-token-crit04"},
    )
    # 404 means auth passed and job lookup ran correctly
    assert response.status_code == 404, (
        f"Expected 404 (job not found), got {response.status_code}. "
        f"Response body: {response.text[:200]}"
    )
