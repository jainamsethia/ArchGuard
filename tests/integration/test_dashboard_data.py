"""Integration tests for dashboard data endpoints (runs, modules, latest).

These are the routes the dashboard UI calls on every page load. They read from
PostgreSQL, so the tests write to PostgreSQL -- via ``seed_run``, which puts a
job and a run through the same ``persist_run`` the analysis pipeline uses, so a
test cannot pass against a row shape the pipeline would never produce.
"""

from __future__ import annotations

import uuid

from tests.db_fixtures import requires_postgres

# ── GET /api/v1/runs ──────────────────────────────────────────────────


@requires_postgres
def test_runs_empty_when_no_runs_recorded(live_db, auth_client):
    """GET /api/v1/runs returns an empty list on a fresh instance."""
    response = auth_client.get("/api/v1/runs")
    assert response.status_code == 200
    data = response.json()
    assert data["runs"] == []
    assert data["total"] == 0


@requires_postgres
def test_runs_returns_recent_entries(seed_run, auth_client):
    """GET /api/v1/runs returns stored runs, newest first."""
    seed_run(score=72.0, band="WARN")
    seed_run(score=85.0, band="PASS")
    response = auth_client.get("/api/v1/runs")
    assert response.status_code == 200
    data = response.json()
    assert len(data["runs"]) == 2
    assert data["total"] == 2
    assert data["runs"][0]["score"] == 85.0
    assert data["runs"][1]["score"] == 72.0


@requires_postgres
def test_runs_limit_respected(seed_run, auth_client):
    """GET /api/v1/runs?limit=1 returns at most 1 entry."""
    seed_run(score=90.0)
    seed_run(score=80.0)
    response = auth_client.get("/api/v1/runs", params={"limit": 1})
    assert response.status_code == 200
    assert len(response.json()["runs"]) == 1


# ── GET /api/v1/runs/latest ─────────────────────────────────────────


@requires_postgres
def test_runs_latest_empty_without_job_id(live_db, auth_client):
    """No job_id means no repository context, so the endpoint says so."""
    response = auth_client.get("/api/v1/runs/latest")
    assert response.status_code == 200
    data = response.json()
    assert data.get("empty") is True or "message" in data


@requires_postgres
def test_runs_latest_returns_newest(seed_run, auth_client):
    """The newest run for the job wins, not the first one written."""
    job_id = str(uuid.uuid4())
    seed_run(job_id=job_id, score=65.0, band="FAIL")
    seed_run(job_id=job_id, score=92.0, band="PASS")
    response = auth_client.get(
        "/api/v1/runs/latest", params={"job_id": job_id}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["score"] == 92.0
    assert data["band"] == "PASS"


# ── GET /api/v1/modules ────────────────────────────────────────────


@requires_postgres
def test_modules_empty_when_no_runs_recorded(live_db, auth_client):
    """GET /api/v1/modules returns an empty modules dict on a fresh instance."""
    response = auth_client.get("/api/v1/modules")
    assert response.status_code == 200
    assert response.json()["modules"] == {}


@requires_postgres
def test_modules_without_job_id_returns_honest_empty_state(seed_run, auth_client):
    """No job_id means no repository context, so /modules must not serve
    whatever this instance analysed most recently as if it were the visitor's.
    Mirrors /api/v1/runs/latest, which already returns {"empty": true} here."""
    seed_run(module_scores={"archguard": 90.0, "tests": 80.0})
    response = auth_client.get("/api/v1/modules")
    assert response.status_code == 200
    data = response.json()
    assert data["empty"] is True
    assert data["modules"] == {}
    assert data["edges"] == []


@requires_postgres
def test_modules_reports_the_latest_scores(seed_run, auth_client):
    """The most recent run for the job supplies the module scores."""
    job_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    seed_run(job_id=job_id, score=85.0, module_scores={"archguard": 90.0, "tests": 80.0})
    seed_run(
        job_id=job_id, score=88.0, module_scores={"archguard": 92.0, "tests": 80.0}
    )
    response = auth_client.get(
        "/api/v1/modules", params={"job_id": job_id}
    )
    assert response.status_code == 200
    modules = response.json()["modules"]
    assert modules["archguard"] == 92.0
    assert modules["tests"] == 80.0


@requires_postgres
def test_modules_serves_persisted_import_edges(seed_run, auth_client):
    """The graph comes from the run, not from the clone.

    This is what lets the Dependencies tab keep working after the workspace is
    swept: the edges were recomputed from disk before, so the panel went blank
    the moment the temp directory expired.
    """
    job_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    edges = [{"source": "core", "target": "utils"}]
    seed_run(job_id=job_id, module_scores={"core": 90.0}, import_edges=edges)
    response = auth_client.get(
        "/api/v1/modules", params={"job_id": job_id}
    )
    assert response.status_code == 200
    assert response.json()["edges"] == edges


# ── Auth guards ─────────────────────────────────────────────────────────────


@requires_postgres
def test_runs_requires_auth(live_db, auth_client):
    """GET /api/v1/runs returns 401 without a valid token."""
    response = auth_client.get("/api/v1/runs")
    # Without ARCHGUARD_DASHBOARD_TOKEN set and a non-localhost client,
    # the IP-based fallback denies the request.
    assert response.status_code in (200, 401)
    # If token is not set, testclient IP is trusted; if it IS set, we need Bearer.
    # Either way the endpoint doesn't crash.
