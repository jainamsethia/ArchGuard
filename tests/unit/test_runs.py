import uuid

import pytest
from fastapi import HTTPException

from archguard.dashboard.routes import runs


def test_get_deps_unknown_job_returns_410(monkeypatch):
    """A syntactically valid job_id whose workspace is gone -> 410, not a crash."""
    with pytest.raises(HTTPException) as exc:
        runs.get_deps(job_id=str(uuid.uuid4()))
    assert exc.value.status_code == 410

def test_get_deps_without_job_returns_400():
    """No job selected -> 400 with actionable detail."""
    with pytest.raises(HTTPException) as exc:
        runs.get_deps(job_id=None)
    assert exc.value.status_code == 400


def test_get_deps_survives_workspace_expiry_via_audit_log(monkeypatch, tmp_path):
    """Simulated expiry: the on-disk clone is gone, but a previous scan was
    persisted to the audit log, so the panel degrades to that instead of 410 --
    the same persisted-fallback contract Modules/Blast Radius/Dep Graph use."""
    from archguard.audit.logger import AuditLogger

    job_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    log_path = tmp_path / "audit.jsonl"
    persisted = {
        "score": 91.0,
        "vulnerable_packages": [],
        "scanned_packages": 42,
        "skipped": False,
        "skip_reason": None,
        "error": None,
    }
    AuditLogger(log_path).log(
        runs._DEPS_EVENT, job_id=job_id, dependency_health=persisted,
    )
    monkeypatch.setattr(runs, "get_audit_path", lambda jid: log_path)

    # No workspace on disk for this job_id -- get_target_path would raise 410.
    result = runs.get_deps(job_id=job_id)
    assert result["scanned_packages"] == 42
    assert result["score"] == 91.0
    assert result["skipped"] is False


def test_persisted_deps_does_not_pollute_run_history(tmp_path):
    """A persisted dependency scan must not surface as an analysis run: doing so
    would inject a scoreless entry into run history, trends and compare-runs."""
    from archguard.audit.logger import AuditLogger

    job_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    log_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(log_path)
    logger.log("analysis_run", job_id=job_id, timestamp="2026-01-01T00:00:00Z",
               score=80.0, module_scores={"core": 80.0})
    logger.log(runs._DEPS_EVENT, job_id=job_id,
               dependency_health={"score": 91.0, "scanned_packages": 42})

    assert len(logger.read_last_n_runs(n=100)) == 1
    assert runs._persisted_deps(log_path, job_id)["scanned_packages"] == 42


def test_runs_job_id_filter(monkeypatch):
    import pathlib
    import tempfile

    from archguard.audit.logger import AuditLogger
    from archguard.dashboard.routes import runs

    with tempfile.TemporaryDirectory() as d:
        log_path = pathlib.Path(d) / "audit.jsonl"
        logger = AuditLogger(log_path)
        logger.log("analysis_run", job_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", timestamp="2026-01-01T00:00:00Z", module_scores={"auth": 0.9})
        logger.log("analysis_run", job_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", timestamp="2026-01-02T00:00:00Z", module_scores={"utils": 0.8})

        monkeypatch.setattr(runs, "get_audit_path", lambda jid: log_path)

        res = runs.get_runs(limit=50, job_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        assert len(res["runs"]) == 1
        assert res["runs"][0]["job_id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

        res = runs.get_modules(job_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        assert "utils" in res["modules"]
        assert "auth" not in res["modules"]

        res = runs.get_module_trends(module="auth", limit=30, job_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        assert len(res["trend"]) == 0

        res2 = runs.get_module_trends(module="auth", limit=30, job_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        assert len(res2["trend"]) == 1

def test_repo_runs(monkeypatch):
    import pathlib
    import tempfile

    from archguard.audit.logger import AuditLogger
    from archguard.dashboard.routes import runs

    with tempfile.TemporaryDirectory() as d:
        from archguard.config import AUDIT_LOG_FILENAME
        log_path = pathlib.Path(d) / AUDIT_LOG_FILENAME
        logger = AuditLogger(log_path)
        logger.log_run('https://github.com/a/repo-a', 'job-a', timestamp='2026-01-01T00:00:00Z', module_scores={})
        logger.log_run('https://github.com/b/repo-b', 'job-b', timestamp='2026-01-02T00:00:00Z', module_scores={})

        orig_cwd = __import__('os').getcwd()
        __import__('os').chdir(d)
        try:
            res = runs.get_repo_runs(repo_url="https://github.com/a/repo-a", limit=50)
            assert res["total"] == 1
            assert res["runs"][0]["repo_url"] == "https://github.com/a/repo-a"
        finally:
            __import__('os').chdir(orig_cwd)


def test_runs_by_job_id_returns_violations_for_compare(monkeypatch):
    """The dashboard's compare-runs feature diffs the violation lists of two
    runs fetched via /api/v1/runs?job_id=. This pins the contract: each run
    returns its violations so the frontend can compute added/resolved/unchanged.
    """
    import pathlib
    import tempfile

    from archguard.audit.logger import AuditLogger
    from archguard.dashboard.routes import runs

    with tempfile.TemporaryDirectory() as d:
        log_path = pathlib.Path(d) / "audit.jsonl"
        logger = AuditLogger(log_path)

        viol_a = [{"layer": 1, "module": "api", "file": "api/routes.py",
                   "line": 1, "severity": "critical", "message": "bad import"}]
        logger.log("analysis_run", job_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                   timestamp="2026-01-01T00:00:00Z", violations=viol_a, score=80.0, band="WATCH")

        viol_b = [{"layer": 1, "module": "api", "file": "api/routes.py",
                   "line": 1, "severity": "critical", "message": "bad import"},
                  {"layer": 2, "module": "core", "file": "", "line": 0,
                   "severity": "high", "message": "fan_out=4 exceeds budget=3"}]
        logger.log("analysis_run", job_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                   timestamp="2026-01-02T00:00:00Z", violations=viol_b, score=60.0, band="WARN")

        monkeypatch.setattr(runs, "get_audit_path", lambda jid: log_path)

        res_a = runs.get_runs(limit=50, job_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        res_b = runs.get_runs(limit=50, job_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        assert len(res_a["runs"]) == 1 and len(res_b["runs"]) == 1
        assert len(res_a["runs"][0]["violations"]) == 1
        assert len(res_b["runs"][0]["violations"]) == 2
        # The diff key the frontend uses (module+layer+message) is present on each.
        for v in res_a["runs"][0]["violations"] + res_b["runs"][0]["violations"]:
            assert {"module", "layer", "message"} <= set(v.keys())
