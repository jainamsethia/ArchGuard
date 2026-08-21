import pytest
from fastapi.testclient import TestClient

from archguard.dashboard.app import app


@pytest.fixture
def client():
    return TestClient(app)

def test_cors_preflight_returns_200(client):
    """OPTIONS preflight for /api/jobs should return 200 with CORS headers."""
    resp = client.options(
        "/api/v1/jobs/validate",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert resp.status_code == 200
    assert "access-control-allow-origin" in resp.headers

def test_cors_header_present_on_get(client):
    """Regular GET /api/runs should include CORS headers for allowed origin."""
    resp = client.get("/api/v1/runs", headers={"Origin": "http://localhost:3000"})

    # Status may be 200 or 401 depending on ARCHGUARD_DASHBOARD_TOKEN — what matters
    # is the CORS header being present
    assert "access-control-allow-origin" in resp.headers
