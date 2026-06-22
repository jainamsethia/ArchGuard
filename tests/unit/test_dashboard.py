import pytest
from fastapi.testclient import TestClient
from archguard.dashboard.app import app
from unittest.mock import patch, MagicMock

# Create a TestClient for our app
client = TestClient(app)


def test_dashboard_version_matches_package_version():
    import importlib.metadata
    from archguard.dashboard.app import app

    assert app.version == importlib.metadata.version("archguard")


@pytest.fixture
def mock_audit_logger():
    # Mock AuditLogger so the endpoint actually works without needing a real DB/file
    with patch("archguard.dashboard.routes.runs.AuditLogger") as mock_logger_cls:
        mock_instance = MagicMock()
        mock_instance.read_last_n_runs.return_value = []
        mock_logger_cls.return_value = mock_instance
        yield mock_instance


def test_api_runs_no_token_configured(mock_audit_logger, monkeypatch):
    """Test that /api/runs returns 200 when no token is configured."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._state import RATE_LIMITS

    RATE_LIMITS.clear()

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

    response = client.get("/api/runs", headers={"Authorization": "Bearer secret-token"})
    assert response.status_code == 200
    assert "runs" in response.json()


def test_api_runs_token_configured_with_incorrect_auth(mock_audit_logger, monkeypatch):
    """Test that /api/runs returns 401 with an incorrect bearer token."""
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "secret-token")

    response = client.get("/api/runs", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing token"


from collections import namedtuple

ClientTuple = namedtuple("ClientTuple", ["host", "port"])


def test_api_runs_remote_no_token_401(mock_audit_logger, monkeypatch):
    """Test that remote IP without token returns 401."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._state import RATE_LIMITS

    RATE_LIMITS.clear()
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", raising=False)

    with patch(
        "starlette.requests.Request.client", new_callable=MagicMock
    ) as mock_client:
        mock_client.host = "192.168.1.100"
        mock_client.port = 12345

        response = client.get("/api/runs")
        assert response.status_code == 401
        assert (
            "Dashboard requires ARCHGUARD_DASHBOARD_TOKEN" in response.json()["detail"]
        )


def test_api_runs_remote_with_token_200(mock_audit_logger, monkeypatch):
    """Test that remote IP with correct token returns 200."""
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "secret-token")

    with patch(
        "starlette.requests.Request.client", new_callable=MagicMock
    ) as mock_client:
        mock_client.host = "192.168.1.100"
        mock_client.port = 12345

        response = client.get(
            "/api/runs", headers={"Authorization": "Bearer secret-token"}
        )
        assert response.status_code == 200


def test_api_runs_limit_exceeds_max_returns_422(mock_audit_logger, monkeypatch):
    """Test that /api/runs returns 422 if limit > 500."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._state import RATE_LIMITS

    RATE_LIMITS.clear()

    response = client.get("/api/runs?limit=999999")
    assert response.status_code == 422
    assert "less than or equal to 500" in response.json()["detail"][0]["msg"]


def test_api_runs_rate_limiting_returns_429(mock_audit_logger, monkeypatch):
    """Test that /api/runs returns 429 after 50 requests in a minute."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._state import RATE_LIMITS

    RATE_LIMITS.clear()

    from archguard.dashboard._state import RATE_LIMITS
    from cachetools import TTLCache

    # Confirm it's a TTLCache instance
    assert isinstance(RATE_LIMITS, TTLCache)

    RATE_LIMITS.clear()

    # Confirm maxsize is respected
    from collections import deque

    for i in range(10001):
        RATE_LIMITS[f"ip-{i}"] = deque()

    assert len(RATE_LIMITS) == 10000

    RATE_LIMITS.clear()

    for _ in range(50):
        response = client.get("/api/runs")
        assert response.status_code == 200

    response = client.get("/api/runs")
    assert response.status_code == 429
    assert response.json()["detail"] == "Too many requests"


def test_api_trends_invalid_module_returns_422(mock_audit_logger, monkeypatch):
    """Test that /api/trends/<invalid-chars> returns 422."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._state import RATE_LIMITS

    RATE_LIMITS.clear()
    response = client.get("/api/trends/invalid_@_module!")
    assert response.status_code == 422
    assert "pattern" in response.json()["detail"][0]["type"]
