from fastapi.testclient import TestClient

from archguard.dashboard.app import app

client = TestClient(app)

def test_deprecation_middleware():
    res = client.get("/api/health")
    assert res.headers.get("Deprecation") == "true"
    assert res.headers.get("Sunset") == "Fri, 01 Jan 2027 00:00:00 GMT"
    assert "rel=\"successor-version\"" in res.headers.get("Link", "")

def test_no_deprecation_on_v1():
    res = client.get("/api/v1/health")
    assert not res.headers.get("Deprecation")

def test_auth_status_deprecated():
    res = client.get("/api/auth/status")
    assert res.headers.get("Deprecation") == "true"

def test_auth_status_v1():
    res = client.get("/api/v1/auth/status")
    assert not res.headers.get("Deprecation")
