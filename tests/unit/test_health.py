import pytest
from fastapi.testclient import TestClient

from archguard.dashboard.app import app

@pytest.fixture
def client():
    return TestClient(app)

def test_health_returns_200(client):
    resp = client.get("/health")
    assert resp.status_code == 200

def test_health_has_required_fields(client):
    resp = client.get("/health")
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "uptime_seconds" in body
    assert "environment" in body

def test_global_exception_handler():
    """Unhandled exception → 500 JSON instead of HTML traceback."""
    from fastapi import Request
    from archguard.dashboard.app import _global_exception_handler
    import asyncio
    
    scope = {
        "type": "http", 
        "method": "GET", 
        "path": "/test-crash",
        "headers": [(b"host", b"testserver")],
        "query_string": b"",
        "server": ("testserver", 80)
    }
    request = Request(scope)
    
    # Call the handler directly
    response = asyncio.run(_global_exception_handler(request, RuntimeError("intentional test crash")))
    
    assert response.status_code == 500
    
    import json
    body = json.loads(response.body.decode())
    assert body["error"] == "Internal server error"
    assert body["type"] == "RuntimeError"
