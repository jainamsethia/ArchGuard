from fastapi.testclient import TestClient
from archguard.dashboard.app import app

def test_api_routes_take_precedence_over_static_mount():
    """Regression guard for WEB-03: StaticFiles must never shadow an explicit API route."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json().get("status") == "ok"  # confirms the real health_check ran, not a 404 static
