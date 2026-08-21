"""End-to-end tests for the /api/v1/risk (Module Blast Radius) route.

Pins the post-relabel contract: the route reports each module's transitive
downstream dependents from the dependency graph — it must NOT pass every .py
file as "changed" (the old behaviour) and must NOT return the deprecated
PRRiskReport field names (modified_modules / downstream_impacts / blocked).
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.db_fixtures import requires_postgres

pytestmark = requires_postgres

# api -> core, utils -> core : core is depended on by api and utils (reach 2).
_CONTRACT = """\
version: "3.0"
modules:
  - name: core
    path: core/
  - name: api
    path: api/
  - name: utils
    path: utils/
"""


@pytest.fixture
def client(monkeypatch, seed_run, auth_client) -> TestClient:
    """A signed-in client whose user owns a job with a workspace on disk.

    The job is seeded for real: the route computes blast radius from the clone
    when no graph was persisted, and it settles ownership before going near the
    filesystem -- otherwise anyone holding a job id could read the structure of
    someone else's repository.
    """
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)

    job_id = seed_run()
    repo_dir = Path(tempfile.gettempdir()) / f"archguard-{job_id}" / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / ".archguard.yml").write_text(_CONTRACT, encoding="utf-8")
    (repo_dir / "core").mkdir()
    (repo_dir / "core" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (repo_dir / "api").mkdir()
    (repo_dir / "api" / "__init__.py").write_text(
        "from core import x\n", encoding="utf-8"
    )
    (repo_dir / "utils").mkdir()
    (repo_dir / "utils" / "__init__.py").write_text(
        "from core import x\n", encoding="utf-8"
    )

    auth_client._archguard_job_id = job_id  # type: ignore[attr-defined]
    yield auth_client
    shutil.rmtree(repo_dir.parent, ignore_errors=True)


def test_blast_radius_reports_downstream_dependents(client: TestClient) -> None:
    """core is depended on by api and utils -> its downstream reach is 2."""
    q = f"?job_id={client._archguard_job_id}"  # type: ignore[attr-defined]
    resp = client.get(f"/api/v1/risk{q}")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # New contract fields.
    for key in ("level", "modules", "hotspots", "threshold", "max_downstream"):
        assert key in data, f"missing {key}"

    by_mod = {m["module"]: m["downstream"] for m in data["modules"]}
    assert by_mod["core"] == 2, by_mod  # both api and utils reach core
    assert by_mod["api"] == 0
    assert by_mod["utils"] == 0
    assert data["max_downstream"] == 2

    # Modules are sorted widest-first.
    assert data["modules"][0]["module"] == "core"


def test_blast_radius_drops_old_pr_risk_fields(client: TestClient) -> None:
    """The deprecated PRRiskReport shape must not leak into the response."""
    q = f"?job_id={client._archguard_job_id}"  # type: ignore[attr-defined]
    data = client.get(f"/api/v1/risk{q}").json()
    for deprecated in ("modified_modules", "downstream_impacts", "blocked", "blocked_reasons", "risk_score", "module_risks", "overall_risk"):
        assert deprecated not in data, f"deprecated field {deprecated} present"


def test_blast_radius_requires_job_context(monkeypatch) -> None:
    """No job_id -> 400 with an actionable message."""
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "test-token-risk")
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", "1")
    from archguard.dashboard.app import app

    client = TestClient(app)
    resp = client.get("/api/v1/risk", headers={"Authorization": "Bearer test-token-risk"})
    assert resp.status_code == 400
    assert "repository context" in resp.json()["detail"]
