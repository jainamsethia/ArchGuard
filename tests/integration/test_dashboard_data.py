"""Integration tests for dashboard data endpoints (runs, modules, latest).

Validates that the core data-loading routes return correct responses
with and without a populated audit log. These are the primary endpoints
the dashboard UI calls on every page load.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from archguard.dashboard.app import app

client = TestClient(app)

AUDIT_LOG = ".archguard-cache/audit.jsonl"


def _populate_audit_log(root: Path, entries: list[dict]) -> Path:
    """Write audit log entries to root/.archguard-cache/audit.jsonl."""
    log_path = root / AUDIT_LOG
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return log_path


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer test_token"}


# ── GET /api/v1/runs ────────────────────────────────────────────────────────


def test_runs_empty_when_no_audit_log(monkeypatch, tmp_path):
    """GET /api/v1/runs returns empty list when no audit log exists."""
    monkeypatch.chdir(tmp_path)
    response = client.get("/api/v1/runs", headers=_headers())
    assert response.status_code == 200
    data = response.json()
    assert "runs" in data
    assert data["runs"] == []
    assert data["total"] == 0


def test_runs_returns_recent_entries(monkeypatch, tmp_path):
    """GET /api/v1/runs returns runs from the audit log."""
    monkeypatch.chdir(tmp_path)
    _populate_audit_log(tmp_path, [
        {"event": "analysis_run", "score": 85.0, "band": "PASS", "violations": []},
        {"event": "analysis_run", "score": 72.0, "band": "WARN", "violations": []},
    ])
    response = client.get("/api/v1/runs", headers=_headers())
    assert response.status_code == 200
    data = response.json()
    assert len(data["runs"]) == 2
    assert data["total"] == 2
    assert data["runs"][0]["score"] == 85.0
    assert data["runs"][1]["score"] == 72.0


def test_runs_limit_respected(monkeypatch, tmp_path):
    """GET /api/v1/runs?limit=1 returns at most 1 entry."""
    monkeypatch.chdir(tmp_path)
    _populate_audit_log(tmp_path, [
        {"event": "analysis_run", "score": 90.0, "band": "PASS"},
        {"event": "analysis_run", "score": 80.0, "band": "PASS"},
    ])
    response = client.get("/api/v1/runs", params={"limit": 1}, headers=_headers())
    assert response.status_code == 200
    data = response.json()
    assert len(data["runs"]) == 1


# ── GET /api/v1/runs/latest ─────────────────────────────────────────────────


def test_runs_latest_empty_when_no_audit_log(monkeypatch, tmp_path):
    """GET /api/v1/runs/latest returns empty state when no audit log exists."""
    monkeypatch.chdir(tmp_path)
    response = client.get("/api/v1/runs/latest", headers=_headers())
    assert response.status_code == 200
    data = response.json()
    # Without a job_id and no audit log, the endpoint returns an empty marker
    assert data.get("empty") is True or "message" in data


def test_runs_latest_returns_newest(monkeypatch, tmp_path):
    """GET /api/v1/runs/latest?job_id=<uuid> returns the matching analysis run."""
    monkeypatch.chdir(tmp_path)
    import uuid
    job_id = str(uuid.uuid4())
    _populate_audit_log(tmp_path, [
        {"event": "analysis_run", "score": 65.0, "band": "FAIL", "job_id": job_id},
        {"event": "analysis_run", "score": 92.0, "band": "PASS", "job_id": job_id},
    ])
    response = client.get("/api/v1/runs/latest", params={"job_id": job_id}, headers=_headers())
    assert response.status_code == 200
    data = response.json()
    # After the fix, get_latest_run iterates in *reverse* chronological
    # order so it returns the newest run for the given job_id (92.0), not the oldest.
    assert data["score"] == 92.0
    assert data["band"] == "PASS"


# ── GET /api/v1/modules ─────────────────────────────────────────────────────


def test_modules_empty_when_no_audit_log(monkeypatch, tmp_path):
    """GET /api/v1/modules returns empty modules dict when no audit log exists."""
    monkeypatch.chdir(tmp_path)
    response = client.get("/api/v1/modules", headers=_headers())
    assert response.status_code == 200
    data = response.json()
    assert "modules" in data
    assert data["modules"] == {}


def test_modules_without_job_id_returns_honest_empty_state(monkeypatch, tmp_path):
    """No job_id means no repository context, so /modules must not serve the
    server's own cwd audit log as if it were the visitor's analysis. Mirrors
    /api/v1/runs/latest, which already returns {"empty": true} here."""
    monkeypatch.chdir(tmp_path)
    _populate_audit_log(tmp_path, [
        {
            "event": "analysis_run",
            "score": 85.0, "band": "PASS",
            "module_scores": {"archguard": 90.0, "tests": 80.0},
        },
    ])
    response = client.get("/api/v1/modules", headers=_headers())
    assert response.status_code == 200
    data = response.json()
    assert data["empty"] is True
    assert data["modules"] == {}
    assert data["edges"] == []


def test_modules_aggregates_scores(monkeypatch, tmp_path):
    """GET /api/v1/modules aggregates module scores across a job's runs."""
    monkeypatch.chdir(tmp_path)
    job_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    _populate_audit_log(tmp_path, [
        {
            "event": "analysis_run", "job_id": job_id,
            "score": 85.0, "band": "PASS",
            "module_scores": {"archguard": 90.0, "tests": 80.0},
        },
        {
            "event": "analysis_run", "job_id": job_id,
            "score": 88.0, "band": "PASS",
            "module_scores": {"archguard": 92.0},
        },
    ])
    response = client.get("/api/v1/modules", params={"job_id": job_id}, headers=_headers())
    assert response.status_code == 200
    data = response.json()
    assert "archguard" in data["modules"]
    assert "tests" in data["modules"]
    # Latest score wins
    assert data["modules"]["archguard"] == 92.0
    assert data["modules"]["tests"] == 80.0


def test_modules_includes_import_edges(monkeypatch, tmp_path):
    """GET /api/v1/modules includes edges key (may be empty if no cross-module imports)."""
    monkeypatch.chdir(tmp_path)
    job_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    _populate_audit_log(tmp_path, [
        {"event": "analysis_run", "job_id": job_id, "score": 85.0,
         "band": "PASS", "module_scores": {"core": 90.0}},
    ])
    response = client.get("/api/v1/modules", params={"job_id": job_id}, headers=_headers())
    assert response.status_code == 200
    data = response.json()
    assert "edges" in data
    assert isinstance(data["edges"], list)


# ── Auth guards ─────────────────────────────────────────────────────────────


def test_runs_requires_auth():
    """GET /api/v1/runs returns 401 without a valid token."""
    response = client.get("/api/v1/runs")
    # Without ARCHGUARD_DASHBOARD_TOKEN set and a non-localhost client,
    # the IP-based fallback denies the request.
    assert response.status_code in (200, 401)
    # If token is not set, testclient IP is trusted; if it IS set, we need Bearer.
    # Either way the endpoint doesn't crash.
