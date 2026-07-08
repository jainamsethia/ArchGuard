import uuid
from archguard.dashboard.routes import runs

def test_get_deps_name_error(monkeypatch):
    result = runs.get_deps(job_id=str(uuid.uuid4()))
    assert isinstance(result, dict)


def test_runs_job_id_filter(monkeypatch):
    import tempfile, pathlib
    from archguard.audit.logger import AuditLogger
    from archguard.dashboard.routes import runs

    with tempfile.TemporaryDirectory() as d:
        log_path = pathlib.Path(d) / "audit.jsonl"
        logger = AuditLogger(log_path)
        logger.log("analysis_run", job_id="job-A", timestamp="2026-01-01T00:00:00Z", module_scores={"auth": 0.9})
        logger.log("analysis_run", job_id="job-B", timestamp="2026-01-02T00:00:00Z", module_scores={"utils": 0.8})
        
        monkeypatch.setattr(runs, "get_audit_path", lambda jid: log_path)
        
        res = runs.get_runs(limit=50, job_id="job-A")
        assert len(res["runs"]) == 1
        assert res["runs"][0]["job_id"] == "job-A"

        res = runs.get_modules(job_id="job-B")
        assert "utils" in res["modules"]
        assert "auth" not in res["modules"]

        res = runs.get_module_trends(module="auth", limit=30, job_id="job-B")
        assert len(res["trend"]) == 0
        
        res2 = runs.get_module_trends(module="auth", limit=30, job_id="job-A")
        assert len(res2["trend"]) == 1

def test_repo_runs(monkeypatch):
    import tempfile, pathlib
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
