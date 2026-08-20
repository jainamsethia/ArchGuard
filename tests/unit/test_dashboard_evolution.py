from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from archguard.dashboard.app import app

client = TestClient(app)


@pytest.fixture
def mock_audit_logger():
    with patch("archguard.dashboard.routes.evolution.AuditLogger") as mock_logger_cls:
        mock_instance = MagicMock()
        # Mock some raw snapshots
        mock_instance.read_last_n_runs.return_value = [
            {
                "timestamp": "2023-01-01T10:00:00Z",
                "score": 70.0,
                "violations": [{"id": 1}],
                "metrics": {"fitness_results": [{"passed": False}]},
            },
            {
                "timestamp": "2023-01-02T10:00:00Z",
                "score": 85.0,
                "violations": [],
                "metrics": {"fitness_results": [{"passed": True}]},
            },
        ]
        mock_logger_cls.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def mock_audit_logger_empty():
    with patch("archguard.dashboard.routes.evolution.AuditLogger") as mock_logger_cls:
        mock_instance = MagicMock()
        mock_instance.read_last_n_runs.return_value = []
        mock_logger_cls.return_value = mock_instance
        yield mock_instance


def test_api_evolution_summary(mock_audit_logger, monkeypatch):
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import RATE_LIMITS

    RATE_LIMITS.clear()

    response = client.get("/api/evolution/summary")
    assert response.status_code == 200
    data = response.json()
    assert "snapshots" in data
    assert len(data["snapshots"]) == 2
    assert "health_trend" in data
    assert data["health_trend"]["classification"] == "improving"


def test_api_evolution_history(mock_audit_logger, monkeypatch):
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import RATE_LIMITS

    RATE_LIMITS.clear()

    response = client.get("/api/evolution/history")
    assert response.status_code == 200
    data = response.json()
    assert "history" in data
    assert data["total"] == 2
    assert len(data["history"]) == 2


def test_api_evolution_trends(mock_audit_logger, monkeypatch):
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import RATE_LIMITS

    RATE_LIMITS.clear()

    response = client.get("/api/evolution/trends")
    assert response.status_code == 200
    data = response.json()
    assert "health_trend" in data
    assert data["health_trend"]["classification"] == "improving"
    assert "fitness_trend" in data
    assert data["fitness_trend"]["classification"] == "improving"


def test_api_evolution_empty(mock_audit_logger_empty, monkeypatch):
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import RATE_LIMITS

    RATE_LIMITS.clear()

    response = client.get("/api/evolution/summary")
    assert response.status_code == 200
    data = response.json()
    # With no runs recorded there is no history to report. The panel now says so
    # explicitly rather than returning an empty trend report, which read like a
    # real measurement of a repository that simply had nothing wrong.
    assert data["insufficient_history"] is True
    assert data["runs_available"] == 0
    assert "not enough scan history" in data["message"].lower()
