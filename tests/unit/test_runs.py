"""Read endpoints in ``routes/runs.py``, against a real database.

These used to seed ``audit.jsonl`` in a temp directory and monkeypatch
``get_audit_path``. The runs live in PostgreSQL now, so the tests seed
PostgreSQL. They skip when ``TEST_DATABASE_URL`` is unset rather than falling
back to a stub -- a query proved only against a fake proves nothing about the
one production runs.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from archguard.dashboard.routes import runs
from tests.db_fixtures import requires_postgres

JOB_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
JOB_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def test_get_deps_without_job_returns_400():
    """No job selected -> 400 with actionable detail."""
    import asyncio

    with pytest.raises(HTTPException) as exc:
        asyncio.run(runs.get_deps(job_id=None))
    assert exc.value.status_code == 400


@requires_postgres
def test_get_deps_unknown_job_returns_410(live_db):
    """A syntactically valid job_id with no stored scan and no workspace -> 410.

    Not a 200 with an empty result: a job we have no record of must not look
    like a job whose dependencies scanned clean.
    """
    import asyncio

    with pytest.raises(HTTPException) as exc:
        asyncio.run(runs.get_deps(job_id=str(uuid.uuid4())))
    assert exc.value.status_code == 410


@requires_postgres
def test_get_deps_survives_workspace_expiry(seed_run):
    """The clone is gone but a scan was stored, so the panel serves the stored
    one -- the same persisted-fallback contract Modules and Blast Radius use."""
    import asyncio

    from archguard.db import store
    from archguard.db.session import session_scope

    job_id = seed_run()
    persisted = {
        "score": 91.0,
        "vulnerable_packages": [],
        "scanned_packages": 42,
        "skipped": False,
        "skip_reason": None,
        "error": None,
    }

    async def _save():
        async with session_scope() as session:
            await store.save_dependency_scan(session, job_id, persisted)

    asyncio.run(_save())

    result = asyncio.run(runs.get_deps(job_id=job_id))
    assert result["scanned_packages"] == 42
    assert result["score"] == 91.0
    assert result["skipped"] is False


@requires_postgres
def test_persisted_deps_does_not_pollute_run_history(seed_run):
    """A stored dependency scan must not surface as an analysis run: doing so
    would inject a scoreless entry into run history, trends and compare-runs.

    They are separate tables now, which is what makes this structurally true
    rather than a filter someone has to remember to apply.
    """
    import asyncio

    from archguard.db import store
    from archguard.db.session import session_scope

    job_id = seed_run(score=80.0, module_scores={"core": 80.0})

    async def _save_and_read():
        async with session_scope() as session:
            await store.save_dependency_scan(
                session, job_id, {"score": 91.0, "scanned_packages": 42}
            )
        async with session_scope() as session:
            return await store.get_runs_for_job(session, job_id, limit=100)

    history = asyncio.run(_save_and_read())
    assert len(history) == 1
    assert history[0]["score"] == 80.0


@requires_postgres
def test_runs_job_id_filter(seed_run):
    """Each read is scoped to its job: one job's modules never answer another's."""
    import asyncio

    seed_run(job_id=JOB_A, module_scores={"auth": 0.9}, modules_analyzed=["auth"])
    seed_run(job_id=JOB_B, module_scores={"utils": 0.8}, modules_analyzed=["utils"])

    res = asyncio.run(runs.get_runs(limit=50, job_id=JOB_A))
    assert len(res["runs"]) == 1
    assert res["runs"][0]["job_id"] == JOB_A

    res = asyncio.run(runs.get_modules(job_id=JOB_B))
    assert "utils" in res["modules"]
    assert "auth" not in res["modules"]

    res = asyncio.run(runs.get_module_trends(module="auth", limit=30, job_id=JOB_B))
    assert len(res["trend"]) == 0

    res2 = asyncio.run(runs.get_module_trends(module="auth", limit=30, job_id=JOB_A))
    assert len(res2["trend"]) == 1


@requires_postgres
def test_repo_runs(seed_run):
    """History is per repository, across jobs.

    This is the query the trend chart needs and could not previously have: the
    audit log could only be string-matched on ``repo_url``, and truncated itself
    at 10 MB, so a repository's history silently disappeared. Two scans of the
    same repository under different job ids must come back as two runs.
    """
    import asyncio

    seed_run(repo_url="https://github.com/a/repo-a", score=70.0)
    seed_run(repo_url="https://github.com/a/repo-a", score=75.0)
    seed_run(repo_url="https://github.com/b/repo-b", score=90.0)

    res = asyncio.run(
        runs.get_repo_runs(repo_url="https://github.com/a/repo-a", limit=50)
    )
    assert res["total"] == 2
    assert {r["repo_url"] for r in res["runs"]} == {"https://github.com/a/repo-a"}


@requires_postgres
def test_runs_by_job_id_returns_violations_for_compare(seed_run):
    """Compare-runs diffs the violation lists of two runs fetched via
    ``/api/v1/runs?job_id=``. Each run must carry its own violations, with the
    module+layer+message key the frontend diffs on.
    """
    import asyncio

    viol_a = [
        {
            "layer": 1,
            "module": "api",
            "file": "api/routes.py",
            "line": 1,
            "severity": "critical",
            "message": "bad import",
        }
    ]
    viol_b = [
        *viol_a,
        {
            "layer": 2,
            "module": "core",
            "file": "",
            "line": 0,
            "severity": "high",
            "message": "fan_out=4 exceeds budget=3",
        },
    ]
    seed_run(job_id=JOB_A, violations=viol_a, score=80.0, band="WATCH")
    seed_run(job_id=JOB_B, violations=viol_b, score=60.0, band="WARN")

    res_a = asyncio.run(runs.get_runs(limit=50, job_id=JOB_A))
    res_b = asyncio.run(runs.get_runs(limit=50, job_id=JOB_B))
    assert len(res_a["runs"]) == 1 and len(res_b["runs"]) == 1
    assert len(res_a["runs"][0]["violations"]) == 1
    assert len(res_b["runs"][0]["violations"]) == 2
    for v in res_a["runs"][0]["violations"] + res_b["runs"][0]["violations"]:
        assert {"module", "layer", "message"} <= set(v.keys())
