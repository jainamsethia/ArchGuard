"""End-to-end tests for the /api/v1/suppressions dashboard routes.

Covers the working-tree suppression management surface (GET/POST/DELETE) and
the DELETE 404 semantics added when the route was refactored onto
``SuppressionStore.delete``. Runs against a real in-process job workspace so
the JSONL store, file locking, and audit logging are exercised unmocked.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """TestClient with token auth enabled and a tmp workspace for a known job_id."""
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "test-token-suppression")
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", "1")
    token = "test-token-suppression"
    auth_headers = {"Authorization": f"Bearer {token}"}

    job_id = str(uuid.uuid4())
    # Resolve the temp dir the same way get_target_path() does (app.py uses
    # tempfile.gettempdir()), and create the workspace it will resolve to.
    repo_dir = Path(tempfile.gettempdir()) / f"archguard-{job_id}" / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / ".archguard.yml").write_text("version: '3.0'\nmodules: []\n", encoding="utf-8")

    from archguard.dashboard.app import app
    client = TestClient(app, raise_server_exceptions=False)
    client.headers.update(auth_headers)
    client._archguard_job_id = job_id  # type: ignore[attr-defined]
    yield client
    shutil.rmtree(repo_dir.parent, ignore_errors=True)


def test_suppression_lifecycle(client: TestClient) -> None:
    """POST then GET then DELETE a suppression end-to-end."""
    job_id = client._archguard_job_id  # type: ignore[attr-defined]
    q = f"?job_id={job_id}"

    # Add a suppression.
    resp = client.post(
        f"/api/v1/suppressions{q}",
        json={"module": "api", "layer": 1, "message": "bad import", "reason": "FP"},
    )
    assert resp.status_code == 200, resp.text

    # List: should contain exactly one active suppression.
    resp = client.get(f"/api/v1/suppressions{q}")
    assert resp.status_code == 200
    sups = resp.json()["suppressions"]
    assert len(sups) == 1
    assert sups[0]["module"] == "api"
    sup_id = sups[0]["id"]

    # Delete it.
    resp = client.request(
        "DELETE",
        f"/api/v1/suppressions{q}",
        data=json.dumps({"suppression_id": sup_id}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200, resp.text

    # List again: empty.
    resp = client.get(f"/api/v1/suppressions{q}")
    assert resp.status_code == 200
    assert resp.json()["suppressions"] == []


def test_delete_missing_suppression_returns_404(client: TestClient) -> None:
    """DELETE of an id that was never present must 404, not silently 200."""
    job_id = client._archguard_job_id  # type: ignore[attr-defined]
    q = f"?job_id={job_id}"

    resp = client.request(
        "DELETE",
        f"/api/v1/suppressions{q}",
        data=json.dumps({"suppression_id": "does-not-exist-00000000"}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 404


def test_add_rejects_invalid_layer(client: TestClient) -> None:
    """Layer outside {1,2,3,4} is rejected with 400."""
    job_id = client._archguard_job_id  # type: ignore[attr-defined]
    q = f"?job_id={job_id}"

    resp = client.post(
        f"/api/v1/suppressions{q}",
        json={"module": "api", "layer": 9, "message": "msg", "reason": "r"},
    )
    assert resp.status_code == 400
