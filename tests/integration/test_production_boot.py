"""What the production boot path does that nothing was checking.

Four defects, all found by tracing an empty environment through to a healthy
web and worker deployment rather than by running anything -- which is the only
way available, since no deployment has been performed.

The worker had no configuration gate, so one with ENVIRONMENT=production and no
DATABASE_URL started happily, drained the queue and failed every job before it
could write a row. It has no HTTP surface and no health check, so nothing
noticed.

Jobs abandoned mid-analysis were never reconciled. arq cancels running tasks on
SIGTERM and `CancelledError` is a `BaseException`, so it escapes the task's own
`except Exception` and the row stays at `analysing` for ever: retention
deliberately never touches an unfinished job, and the progress stream only
stops when the stored status is terminal -- so the browser watching one waited
on a row nothing would write again.

`ARCHGUARD_DATA_DIR` was read only by the two probes. The startup gate
probe-writes it *because* the audit logger swallows its own write failures, so
pointing it anywhere but the default made the gate validate a directory the
application never touched -- the one defence against a permanently silent audit
log, checking the wrong path.

And migrations took no lock, despite the entrypoint's comment saying Alembic
does. Both containers run `alembic upgrade head` on deploy.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from tests.db_fixtures import requires_postgres

pytestmark = pytest.mark.integration


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------- the worker's configuration


def test_the_worker_runs_the_production_gate():
    """Statically, because starting an arq worker in a test would be testing
    arq. What is asserted is that the call exists on the startup path."""
    import inspect

    from archguard.worker import main

    source = inspect.getsource(main.startup)

    assert "validate_configuration" in source, (
        "the worker starts without the gate that stops the web process serving "
        "traffic when it is misconfigured"
    )


def test_the_gate_is_the_same_one_the_web_process_runs():
    """One gate, not a second opinion that can drift from it."""
    import inspect

    from archguard.dashboard import app
    from archguard.worker import main

    assert "validate_configuration" in inspect.getsource(app._lifespan)
    assert "validate_configuration" in inspect.getsource(main.startup)


# ------------------------------------------------------ abandoned jobs


@pytest.fixture()
def account(live_db):
    from archguard.db import store
    from archguard.db.session import session_scope

    async def _go():
        async with session_scope() as session:
            user = await store.upsert_user(session, github_id=9301, login="boot")
            return user.id

    return _run(_go())


async def _job(store, session_scope, user_id, *, status: str, age_seconds: float):
    from archguard.db.models import Job

    async with session_scope() as session:
        job = await store.create_job(session, "https://github.com/x/boot", user_id=user_id)
        job_id = job.id
    async with session_scope() as session:
        await store.set_job_status(session, job_id, status)
    async with session_scope() as session:
        row = await session.get(Job, job_id)
        row.created_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
        if status not in ("complete", "failed"):
            row.completed_at = None
    return job_id


@requires_postgres
def test_a_job_abandoned_mid_analysis_is_marked_failed(account):
    """Otherwise it sits at `analysing` for ever and the stream never ends."""
    from archguard.db import store
    from archguard.db.models import Job
    from archguard.db.session import session_scope

    async def scenario():
        job_id = await _job(store, session_scope, account, status="analysing", age_seconds=5000)
        async with session_scope() as session:
            count = await store.fail_stalled_jobs(session, older_than_seconds=960)
        async with session_scope() as session:
            row = await session.get(Job, job_id)
            return count, row.status, row.error, row.completed_at

    count, status, error, completed = scenario_result = _run(scenario())
    assert count == 1
    assert status == "failed"
    assert error and "restart" in error.lower(), error
    assert completed is not None, "a terminal status with no completed_at is still unfinished"
    assert scenario_result


@requires_postgres
def test_a_job_that_might_still_be_running_is_left_alone(account):
    """The bound has to exceed the job timeout.

    A second worker may legitimately be running this right now, and a shorter
    window would have one worker's startup kill another's work.
    """
    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        await _job(store, session_scope, account, status="analysing", age_seconds=60)
        async with session_scope() as session:
            return await store.fail_stalled_jobs(session, older_than_seconds=960)

    assert _run(scenario()) == 0, "a job young enough to still be running was reaped"


@requires_postgres
def test_a_finished_job_is_not_touched(account):
    from archguard.db import store
    from archguard.db.models import Job
    from archguard.db.session import session_scope

    async def scenario():
        job_id = await _job(store, session_scope, account, status="complete", age_seconds=99999)
        async with session_scope() as session:
            count = await store.fail_stalled_jobs(session, older_than_seconds=960)
        async with session_scope() as session:
            return count, (await session.get(Job, job_id)).status

    count, status = _run(scenario())
    assert count == 0
    assert status == "complete", "a completed job was rewritten as failed"


@requires_postgres
def test_reconciling_twice_changes_nothing(account):
    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        await _job(store, session_scope, account, status="cloning", age_seconds=5000)
        async with session_scope() as session:
            first = await store.fail_stalled_jobs(session, older_than_seconds=960)
        async with session_scope() as session:
            second = await store.fail_stalled_jobs(session, older_than_seconds=960)
        return first, second

    first, second = _run(scenario())
    assert (first, second) == (1, 0)


def test_the_worker_reconciles_on_startup():
    import inspect

    from archguard.worker import main

    source = inspect.getsource(main.startup)

    assert "fail_stalled_jobs" in source
    assert "job_timeout" in source, (
        "the reconciliation window must be derived from the job timeout, or a "
        "worker starting can reap a job another worker is running"
    )


# ------------------------------------------------------ the data directory


def test_the_audit_log_goes_where_the_gate_probes(tmp_path, monkeypatch):
    """The probe exists because this logger swallows its own write failures.

    Pointing ARCHGUARD_DATA_DIR anywhere but the default used to make the gate
    validate a directory nothing wrote to, which is the one defence against a
    permanently silent audit log checking the wrong path.
    """
    monkeypatch.setenv("ARCHGUARD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ARCHGUARD_AUDIT_SECRET", "boot-test-secret")

    from archguard.audit.logger import AuditLogger

    AuditLogger().log("analysis_run", score=91.0)

    written = list(tmp_path.rglob("audit.jsonl"))
    assert written, f"nothing was written under the configured data dir: {list(tmp_path.iterdir())}"


def test_the_default_is_unchanged_when_the_variable_is_unset(tmp_path, monkeypatch):
    """The default has to keep working: it is what the container image and
    Render's mountPath both assume."""
    monkeypatch.delenv("ARCHGUARD_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    from archguard.audit.logger import _default_log_path

    assert _default_log_path().as_posix().endswith(".archguard-cache/audit.jsonl")


# ------------------------------------------------------------- migrations


def test_migrations_take_a_lock():
    """Both containers run `alembic upgrade head` on deploy.

    On a first deploy against an empty database they compute the same plan at
    the same time; the loser fails with "relation already exists" and exits
    non-zero, which is a failed release on one platform and a restart loop on
    the other. The entrypoint's comment claimed Alembic locks for the duration.
    It does not.
    """
    from pathlib import Path

    env = Path("archguard/db/migrations/env.py").read_text(encoding="utf-8")

    assert "pg_advisory_xact_lock" in env, (
        "concurrent migrators are unserialised"
    )
    # Transaction-scoped, so a crashed migrator cannot leave the next deploy
    # blocked on a lock nobody holds.
    assert "pg_advisory_lock(" not in env, (
        "a session-scoped lock outlives a crash and blocks the next deploy"
    )
