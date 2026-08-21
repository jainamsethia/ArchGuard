"""Evolution endpoints, against a real database.

These used to mock ``AuditLogger`` and hand the route two invented run dicts,
which meant the assertions never exercised the query that produces them. The
runs are seeded in PostgreSQL now and read back the way the dashboard reads
them -- which is also the only way to prove the thing that changed here: two
scans of one repository, under different job ids, correlate into a trend.
"""

from __future__ import annotations

import pytest

from tests.db_fixtures import requires_postgres

REPO = "https://github.com/example/evolving"


@pytest.fixture
def improving_repo(seed_run, monkeypatch):
    """Two scans of one repository, health rising between them.

    Returns the second scan's job id: the dashboard always holds a job id, and
    the endpoint resolves it to the repository so the trend spans both scans.
    """
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import reset_rate_limits

    reset_rate_limits()
    seed_run(
        repo_url=REPO,
        score=70.0,
        violations=[
            {"layer": 1, "module": "api", "severity": "high", "message": "bad import"}
        ],
        metrics={"fitness_results": [{"passed": False}]},
    )
    return seed_run(
        repo_url=REPO,
        score=85.0,
        violations=[],
        metrics={"fitness_results": [{"passed": True}]},
    )


@requires_postgres
def test_api_evolution_summary(improving_repo, auth_client):
    response = auth_client.get(f"/api/evolution/summary?job_id={improving_repo}")
    assert response.status_code == 200
    data = response.json()
    assert "snapshots" in data
    assert len(data["snapshots"]) == 2
    assert "health_trend" in data
    assert data["health_trend"]["classification"] == "improving"


@requires_postgres
def test_api_evolution_history(improving_repo, auth_client):
    response = auth_client.get(f"/api/evolution/history?job_id={improving_repo}")
    assert response.status_code == 200
    data = response.json()
    assert "history" in data
    assert data["total"] == 2
    assert len(data["history"]) == 2


@requires_postgres
def test_api_evolution_trends(improving_repo, auth_client):
    response = auth_client.get(f"/api/evolution/trends?job_id={improving_repo}")
    assert response.status_code == 200
    data = response.json()
    assert "health_trend" in data
    assert data["health_trend"]["classification"] == "improving"
    assert "fitness_trend" in data
    assert data["fitness_trend"]["classification"] == "improving"


@requires_postgres
def test_history_is_scoped_to_one_repository(seed_run, monkeypatch, auth_client):
    """Another repository's scans must not pad this one's trend.

    Worth pinning explicitly: the endpoint used to read a server-wide log, and
    every run in it, so a busy instance produced a trend chart that mixed
    unrelated projects together and called it history.
    """
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import reset_rate_limits

    reset_rate_limits()
    seed_run(repo_url="https://github.com/other/project", score=10.0)
    seed_run(repo_url="https://github.com/other/project", score=20.0)
    mine = seed_run(repo_url=REPO, score=99.0)

    response = auth_client.get(f"/api/evolution/summary?job_id={mine}")
    assert response.status_code == 200
    data = response.json()
    assert data["insufficient_history"] is True
    assert data["runs_available"] == 1, "only this repository's scan may count"
    assert data["repo_url"] == REPO


@requires_postgres
def test_api_evolution_empty(live_db, monkeypatch, auth_client):
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import reset_rate_limits

    reset_rate_limits()

    response = auth_client.get("/api/evolution/summary")
    assert response.status_code == 200
    data = response.json()
    # With no runs recorded there is no history to report. The panel now says so
    # explicitly rather than returning an empty trend report, which read like a
    # real measurement of a repository that simply had nothing wrong.
    assert data["insufficient_history"] is True
    assert data["runs_available"] == 0
    assert "not enough scan history" in data["message"].lower()
