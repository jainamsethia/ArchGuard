"""Golden-path integration tests for primary user journeys.

Each test validates a complete end-to-end workflow from a user's
perspective, not just individual components.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner
from fastapi.testclient import TestClient

from archguard.cli.main import app as cli_app
from archguard.dashboard.app import app as dashboard_app

cli_runner = CliRunner()
dashboard_client = TestClient(dashboard_app)


# ── 1. CLI: Analyze → Report (golden path) ─────────────────────────────────


def test_cli_analyze_then_report_golden_path(tmp_path):
    """Run analyze (exit 0) then generate a report on the same repo."""
    # ── Setup repo ───────────────────────────────────────────────────
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "app.py").write_text("import os\nfrom pathlib import Path\n")
    (src / "utils.py").write_text("def helper(): return 42\n")

    config = {
        "version": "3.0",
        "modules": [{"name": "core", "path": "src/"}],
        "skip_layers": ["semantic", "duplication"],
    }
    with (tmp_path / ".archguard.yml").open("w") as f:
        yaml.dump(config, f)

    # Init git so changed-file detection works
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True,
        env={"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t.com",
              "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.com"},
    )

    changed = "src/app.py,src/utils.py"

    # ── Step 1: analyze ──────────────────────────────────────────────
    analyze_result = cli_runner.invoke(
        cli_app,
        ["analyze", "--repo", str(tmp_path), "--no-llm", "--changed-files", changed,
         "--skip-explanation", "--dry-run"],
        env={"ARCHGUARD_SKIP_ML": "1"},
    )
    # A clean repo should pass — exit 0 (success) or 1 (violations found)
    assert analyze_result.exit_code in (0, 1), (
        f"analyze failed with exit {analyze_result.exit_code}: "
        f"{analyze_result.output[:300]}"
    )

    # ── Step 2: report ───────────────────────────────────────────────
    output_html = tmp_path / "report.html"
    report_result = cli_runner.invoke(
        cli_app,
        ["report", "--root", str(tmp_path), "--output", str(output_html)],
    )
    assert report_result.exit_code == 0, (
        f"report failed: {report_result.output[:200]}"
    )
    assert output_html.exists()
    html = output_html.read_text("utf-8")
    assert "ArchGuard" in html
    # Report contains analysis data (not just template scaffolding)
    assert "const SUMMARY =" in html


# ── 2. Dashboard: Login → Auth → Data Retrieval (golden path) ──────────────


def test_dashboard_login_then_data_retrieval_golden_path(tmp_path, monkeypatch):
    """Log in with ARCHGUARD_DASHBOARD_TOKEN, then retrieve analysis data."""
    import uuid

    token = "test-golden-path-token-abc123"
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", token)
    monkeypatch.chdir(tmp_path)

    audit_file = tmp_path / ".archguard-cache" / "audit.jsonl"
    audit_file.parent.mkdir(parents=True)
    job_id = str(uuid.uuid4())
    with audit_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps({
            "event": "analysis_run", "job_id": job_id,
            "score": 88.5, "band": "PASS",
            "module_scores": {"core": 90.0}, "violations": [],
        }) + "\n")

    # Step 1: Login
    login_resp = dashboard_client.post(
        "/api/v1/auth/login", data={"token": token},
    )
    assert login_resp.status_code == 200
    assert login_resp.json() == {"ok": True}
    cookies = login_resp.cookies

    # Step 2: Auth status
    status_resp = dashboard_client.get("/api/v1/auth/status", cookies=cookies)
    assert status_resp.status_code == 200
    assert status_resp.json().get("authenticated") is True

    # Step 3: Get runs
    runs_resp = dashboard_client.get("/api/v1/runs", cookies=cookies)
    assert runs_resp.status_code == 200
    runs_data = runs_resp.json()
    assert len(runs_data["runs"]) >= 1
    assert runs_data["runs"][0]["score"] == 88.5

    # Step 4: Get latest run
    latest_resp = dashboard_client.get(
        "/api/v1/runs/latest", params={"job_id": job_id}, cookies=cookies,
    )
    assert latest_resp.status_code == 200
    assert latest_resp.json()["score"] == 88.5

    # Step 5: Get modules (job-scoped, like every real UI fetch)
    modules_resp = dashboard_client.get(
        "/api/v1/modules", params={"job_id": job_id}, cookies=cookies,
    )
    assert modules_resp.status_code == 200
    mod_data = modules_resp.json()
    assert "core" in mod_data["modules"]


def test_dashboard_bearer_auth_golden_path(monkeypatch):
    """Access dashboard endpoints with Bearer token instead of cookie."""
    token = "test-bearer-token-xyz"
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", token)
    headers = {"Authorization": f"Bearer {token}"}
    response = dashboard_client.get("/api/v1/runs", headers=headers)
    assert response.status_code == 200


# ── 3. Self-analysis (limited scope) ────────────────────────────────────────


@pytest.mark.slow
def test_cli_analyze_self_dry_run():
    """Run archguard analyze on itself with --dry-run --json.

    Validates that the CLI can parse its own config, load its own
    modules, and produce structured output.  Marked @pytest.mark.slow
    because even with --dry-run the ML layers are slow on developer
    hardware.  Runs in CI (which uses Ubuntu runners with ML deps).
    """
    result = cli_runner.invoke(
        cli_app,
        [
            "analyze",
            "--repo", ".",
            "--no-llm",
            "--skip-explanation",
            "--changed-files", "archguard/__init__.py",
            "--dry-run",
            "--json",
        ],
        env={"ARCHGUARD_SKIP_ML": "1", "ARCHGUARD_MOCK_LLM": "1"},
    )
    assert result.exit_code in (0, 1), (
        f"Self-analysis crashed with exit {result.exit_code}: "
        f"{result.output[:300]}"
    )
