from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from archguard.dashboard.app import app
from archguard.evolution.snapshots import CommitHealthSnapshot, EvolutionReport

client = TestClient(app)


@pytest.fixture
def mock_tracker():
    with patch("archguard.evolution.tracker.ArchitectureEvolutionTracker") as mock_cls:
        tracker_instance = MagicMock()

        # Create some fake snapshots
        s1 = CommitHealthSnapshot(
            sha="abc1234",
            committed_at="2023-01-01T10:00:00Z",
            health_score=90.0,
            composite_score=90.0,
            layer_scores={},
            violation_count=2,
            author="Alice",
            message="Init",
        )
        s2 = CommitHealthSnapshot(
            sha="def5678",
            committed_at="2023-01-02T10:00:00Z",
            health_score=80.0,
            composite_score=80.0,
            layer_scores={},
            violation_count=5,
            author="Bob",
            message="Add bug",
        )

        report = EvolutionReport(snapshots=[s1, s2])
        tracker_instance.analyze_history.return_value = report
        mock_cls.return_value = tracker_instance
        yield mock_cls


def test_start_evolution(mock_tracker):
    # Mocking check_token logic by patching if needed, but dashboard tests usually just pass
    response = client.post("/api/v1/evolution/analyze", json={"max_commits": 5})
    # If 401, we might need a token or mock check_token. Fast API test client
    # Usually in this codebase ARCHGUARD_DASHBOARD_TOKEN env var is not set in tests.
    if response.status_code == 401:
        # In case the rate_limiter or token checker fails, but other endpoints return 200 in unit tests
        pass

    # Let's bypass token by patching os.environ in the test or setting it, but let's just assert 200 if possible.
    # The app code has `check_token` which skips if ARCHGUARD_DASHBOARD_TOKEN is not set and host is testclient.
    assert response.status_code == 200
    data = response.json()
    assert "snapshots" in data
    assert len(data["snapshots"]) == 2
    assert "debt_velocity" in data
    assert "trend_direction" in data
    assert "score_range" in data
    assert data["commits_analyzed"] == 2


def test_get_latest_evolution(mock_tracker):
    client.post("/api/v1/evolution/analyze", json={"max_commits": 5})

    response = client.get("/api/v1/evolution/latest")
    assert response.status_code == 200
    data = response.json()
    assert "snapshots" in data
    assert len(data["snapshots"]) == 2
    assert data["commits_analyzed"] == 2


def test_start_evolution_error_handling():
    with patch("archguard.evolution.tracker.ArchitectureEvolutionTracker") as mock_cls:
        mock_cls.side_effect = Exception("Tracker error")
        response = client.post("/api/v1/evolution/analyze", json={"max_commits": 5})
        assert response.status_code == 200
        data = response.json()
        assert "error" in data
        assert data["error"] == "analysis_failed"
        assert "message" in data
        assert data["commits_analyzed"] == 0
