"""Analysis history does not grow forever.

Nothing expired. Runs, findings, jobs, dependency scans and file hashes
accumulated from the first analysis an account ever ran until the account was
deleted, and the privacy policy could not state a retention period because
there was not one.

The policy is deliberately small, and the shape of it is the argument:

* A completed run and everything hanging off it is kept for a bounded window,
  long enough for the trend chart and Compare Runs to be worth having.
* A job that has not finished is never touched, whatever its age. A queue that
  is stuck is not a reason to delete the work.
* File hashes are dropped only when the repository they belong to has no runs
  left. They are the incremental cache, they are keyed by repository rather
  than by account, and deleting them early only costs the next scan its reuse.
* Configuration is not history. A watched repository, its threshold and its
  webhook survive regardless of how old its last run was -- expiring a watch
  because nothing regressed for three months would stop the monitoring the
  moment it had been quietly working.

Tenancy is not a special case here: the sweep selects on age, never on an
account, so it either deletes an expired row for everybody or for nobody. The
tests below check that it cannot reach a second user's recent data.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from tests.db_fixtures import requires_postgres

pytestmark = pytest.mark.integration


def _run(coro):
    return asyncio.run(coro)


async def _seed_run(store, session_scope, user_id, repo_url, *, age_days: float,
                    status: str = "complete"):
    """A finished job with a run, findings and a dependency scan, aged."""
    from archguard.db.models import Job, Run

    async with session_scope() as session:
        job = await store.create_job(session, repo_url, user_id=user_id)
        job_id = job.id
    async with session_scope() as session:
        await store.set_job_status(session, job_id, status)
        await store.persist_run(
            session,
            job_id,
            {
                "repo_url": repo_url,
                "score": 80.0,
                "band": "PASS",
                "violations": [
                    {"layer": 2, "module": "m", "severity": "high", "message": "old finding"}
                ],
                "module_scores": {"m": 80.0},
                "modules_analyzed": ["m"],
            },
        )
    async with session_scope() as session:
        await store.save_dependency_scan(
            session, job_id, user_id, {"score": 99.0, "scanned_packages": 3}
        )

    when = datetime.now(UTC) - timedelta(days=age_days)
    async with session_scope() as session:
        job = await session.get(Job, job_id)
        job.created_at = when
        if status == "complete":
            job.completed_at = when
        rows = (await session.execute(
            __import__("sqlalchemy").select(Run).where(Run.job_id == job_id)
        )).scalars().all()
        for row in rows:
            row.created_at = when
    return job_id


@pytest.fixture()
def two_accounts(live_db):
    from archguard.db import store
    from archguard.db.session import session_scope

    async def _go():
        async with session_scope() as session:
            a = await store.upsert_user(session, github_id=8801, login="ret-a")
            b = await store.upsert_user(session, github_id=8802, login="ret-b")
            return a.id, b.id

    return _run(_go())


async def _counts(store, session_scope, user_id):
    from sqlalchemy import func, select

    from archguard.db.models import Job, Run

    async with session_scope() as session:
        jobs = (await session.execute(
            select(func.count()).select_from(Job).where(Job.user_id == user_id)
        )).scalar_one()
        runs = (await session.execute(
            select(func.count()).select_from(Run).where(Run.user_id == user_id)
        )).scalar_one()
    return {"jobs": jobs, "runs": runs}


# ------------------------------------------------------------- the policy


@requires_postgres
def test_an_old_completed_run_is_removed(two_accounts):
    from archguard.db import store
    from archguard.db.session import session_scope

    alice, _ = two_accounts

    async def scenario():
        await _seed_run(store, session_scope, alice, "https://github.com/x/old", age_days=200)
        async with session_scope() as session:
            removed = await store.purge_expired_runs(session, retain_days=90)
        return removed, await _counts(store, session_scope, alice)

    removed, counts = _run(scenario())

    assert removed["runs"] >= 1, removed
    assert counts["runs"] == 0, "an expired run survived the sweep"


@requires_postgres
def test_a_recent_run_is_left_alone(two_accounts):
    """The half that makes it a retention policy rather than a delete."""
    from archguard.db import store
    from archguard.db.session import session_scope

    alice, _ = two_accounts

    async def scenario():
        await _seed_run(store, session_scope, alice, "https://github.com/x/new", age_days=3)
        async with session_scope() as session:
            await store.purge_expired_runs(session, retain_days=90)
        return await _counts(store, session_scope, alice)

    assert _run(scenario())["runs"] == 1


@requires_postgres
def test_findings_go_with_the_run_they_belong_to(two_accounts):
    """A violation outliving its run is a finding attached to nothing."""
    from sqlalchemy import func, select

    from archguard.db import store
    from archguard.db.models import Violation
    from archguard.db.session import session_scope

    alice, _ = two_accounts

    async def scenario():
        await _seed_run(store, session_scope, alice, "https://github.com/x/find", age_days=200)
        async with session_scope() as session:
            before = (await session.execute(
                select(func.count()).select_from(Violation)
            )).scalar_one()
            await store.purge_expired_runs(session, retain_days=90)
        async with session_scope() as session:
            after = (await session.execute(
                select(func.count()).select_from(Violation)
            )).scalar_one()
        return before, after

    before, after = _run(scenario())
    assert before >= 1
    assert after == 0, "findings survived the run they described"


@requires_postgres
def test_a_job_that_never_finished_is_never_swept(two_accounts):
    """A stuck queue is not a reason to delete somebody's work.

    Age alone would take a job that has been sitting in `analysing` since a
    worker died -- exactly the row an operator needs in order to find out why.
    """
    from archguard.db import store
    from archguard.db.session import session_scope

    alice, _ = two_accounts

    async def scenario():
        await _seed_run(
            store, session_scope, alice, "https://github.com/x/stuck",
            age_days=400, status="analysing",
        )
        async with session_scope() as session:
            await store.purge_expired_runs(session, retain_days=90)
        return await _counts(store, session_scope, alice)

    assert _run(scenario())["jobs"] == 1, "an unfinished job was deleted for being old"


@requires_postgres
def test_a_watch_survives_its_history(two_accounts):
    """Configuration is not history.

    Expiring a watch because nothing regressed for three months would stop the
    monitoring at the moment it had been quietly working.
    """
    from archguard.db import store
    from archguard.db.session import session_scope

    alice, _ = two_accounts
    url = "https://github.com/x/watched-old"

    async def scenario():
        await _seed_run(store, session_scope, alice, url, age_days=300)
        async with session_scope() as session:
            await store.watch_repository(session, alice, url, health_drop_threshold=2.0)
        async with session_scope() as session:
            await store.purge_expired_runs(session, retain_days=90)
        async with session_scope() as session:
            return await store.list_watched(session, alice)

    watched = _run(scenario())
    assert len(watched) == 1, "the sweep removed a watched repository"
    assert watched[0]["health_drop_threshold"] == 2.0, "it kept the row and lost the settings"


@requires_postgres
def test_file_hashes_go_only_when_the_repository_has_no_runs_left(two_accounts):
    """The incremental cache, which is keyed by repository rather than account.

    Dropping it early costs the next scan its reuse and nothing else, so the
    rule is the conservative one: only when nothing refers to the repository.
    """
    from sqlalchemy import func, select

    from archguard.db import store
    from archguard.db.models import FileHash
    from archguard.db.session import session_scope

    alice, _ = two_accounts
    url = "https://github.com/x/hashes"

    async def scenario():
        await _seed_run(store, session_scope, alice, url, age_days=200)
        async with session_scope() as session:
            repo = await store.upsert_repository(session, url)
            await store.save_file_hashes(session, repo.id, {"a.py": "d" * 64})
        async with session_scope() as session:
            before = (await session.execute(
                select(func.count()).select_from(FileHash)
            )).scalar_one()
            await store.purge_expired_runs(session, retain_days=90)
        async with session_scope() as session:
            after = (await session.execute(
                select(func.count()).select_from(FileHash)
            )).scalar_one()
        return before, after

    before, after = _run(scenario())
    assert before == 1
    assert after == 0, "the cache outlived every run that could use it"


@requires_postgres
def test_file_hashes_stay_while_a_run_still_refers_to_the_repository(two_accounts):
    from sqlalchemy import func, select

    from archguard.db import store
    from archguard.db.models import FileHash
    from archguard.db.session import session_scope

    alice, _ = two_accounts
    url = "https://github.com/x/hashes-kept"

    async def scenario():
        await _seed_run(store, session_scope, alice, url, age_days=200)
        await _seed_run(store, session_scope, alice, url, age_days=1)
        async with session_scope() as session:
            repo = await store.upsert_repository(session, url)
            await store.save_file_hashes(session, repo.id, {"a.py": "d" * 64})
        async with session_scope() as session:
            await store.purge_expired_runs(session, retain_days=90)
        async with session_scope() as session:
            return (await session.execute(
                select(func.count()).select_from(FileHash)
            )).scalar_one()

    assert _run(scenario()) == 1, "the cache was dropped while a run still needed it"


# ------------------------------------------------------- safety properties


@requires_postgres
def test_the_sweep_cannot_reach_another_accounts_recent_data(two_accounts):
    """It selects on age, never on an account -- so this is really a check that
    nothing about one user's expiry touches another user's rows."""
    from archguard.db import store
    from archguard.db.session import session_scope

    alice, bob = two_accounts

    async def scenario():
        await _seed_run(store, session_scope, alice, "https://github.com/x/a-old", age_days=200)
        await _seed_run(store, session_scope, bob, "https://github.com/x/b-new", age_days=2)
        async with session_scope() as session:
            await store.purge_expired_runs(session, retain_days=90)
        return (
            await _counts(store, session_scope, alice),
            await _counts(store, session_scope, bob),
        )

    a, b = _run(scenario())
    assert a["runs"] == 0
    assert b["runs"] == 1, "the sweep deleted another account's recent run"


@requires_postgres
def test_running_it_twice_changes_nothing_the_second_time(two_accounts):
    """Idempotent, because a cron that runs twice after a retry must not behave
    differently the second time."""
    from archguard.db import store
    from archguard.db.session import session_scope

    alice, _ = two_accounts

    async def scenario():
        await _seed_run(store, session_scope, alice, "https://github.com/x/twice", age_days=200)
        async with session_scope() as session:
            first = await store.purge_expired_runs(session, retain_days=90)
        async with session_scope() as session:
            second = await store.purge_expired_runs(session, retain_days=90)
        return first, second

    first, second = _run(scenario())
    assert first["runs"] >= 1
    assert second["runs"] == 0, "the second sweep deleted something again"


@requires_postgres
def test_the_sweep_is_bounded(two_accounts):
    """One transaction must not try to delete a year of history at once."""
    from archguard.db import store
    from archguard.db.session import session_scope

    alice, _ = two_accounts

    async def scenario():
        for n in range(4):
            await _seed_run(
                store, session_scope, alice, f"https://github.com/x/bounded{n}", age_days=200
            )
        async with session_scope() as session:
            return await store.purge_expired_runs(session, retain_days=90, limit=2)

    removed = _run(scenario())
    assert removed["runs"] == 2, f"the limit was not honoured: {removed}"


@requires_postgres
def test_it_reports_what_it_removed(two_accounts):
    """A cleanup nobody can see is one nobody notices failing."""
    from archguard.db import store
    from archguard.db.session import session_scope

    alice, _ = two_accounts

    async def scenario():
        await _seed_run(store, session_scope, alice, "https://github.com/x/counted", age_days=200)
        async with session_scope() as session:
            return await store.purge_expired_runs(session, retain_days=90)

    removed = _run(scenario())
    for key in ("runs", "jobs", "file_hashes"):
        assert key in removed, removed
        assert isinstance(removed[key], int)


@requires_postgres
def test_account_deletion_still_removes_everything(two_accounts):
    """Retention must not have quietly changed what deleting an account does."""
    from archguard.db import store
    from archguard.db.session import session_scope

    alice, _ = two_accounts

    async def scenario():
        await _seed_run(store, session_scope, alice, "https://github.com/x/del", age_days=1)
        async with session_scope() as session:
            await store.watch_repository(session, alice, "https://github.com/x/del")
        async with session_scope() as session:
            await store.delete_user(session, alice)
        return await _counts(store, session_scope, alice)

    counts = _run(scenario())
    assert counts == {"jobs": 0, "runs": 0}


# ------------------------------------------------------------ the schedule


def test_the_sweep_is_scheduled_on_the_existing_worker():
    """Reusing the worker's cron rather than adding a second scheduler."""
    from archguard.worker.main import WorkerSettings

    names = {getattr(c, "name", "") or str(c) for c in WorkerSettings.cron_jobs}
    assert any("purge" in n or "retention" in n for n in names), (
        f"no retention job on the worker's schedule: {names}"
    )


def test_the_retention_window_is_configurable_and_documented():
    """A period the code enforces and no document states is not a policy."""
    from pathlib import Path

    from archguard.worker.retention import RETENTION_DAYS

    assert RETENTION_DAYS >= 1
    example = (Path(__file__).resolve().parents[2] / ".env.example").read_text(
        encoding="utf-8"
    )
    assert "ARCHGUARD_RETENTION_DAYS" in example


@requires_postgres
def test_the_scheduled_task_actually_deletes(two_accounts):
    """The cron entry, not just the store function underneath it.

    A task that is scheduled but never wired to the query would leave the
    policy documented and unenforced, which is worse than not claiming one.
    """
    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.worker.retention import purge_expired_data

    alice, _ = two_accounts

    async def scenario():
        await _seed_run(store, session_scope, alice, "https://github.com/x/cron", age_days=200)
        removed = await purge_expired_data()
        return removed, await _counts(store, session_scope, alice)

    removed, counts = _run(scenario())

    assert removed >= 1, "the scheduled task deleted nothing"
    assert counts["runs"] == 0


@requires_postgres
def test_the_scheduled_task_survives_a_broken_database(monkeypatch):
    """A cron that throws takes the worker's scheduler with it.

    The watch sweep runs an hour earlier on the same schedule; retention
    failing must not stop it running tomorrow.
    """
    from archguard.worker import retention

    async def _explode(*_a, **_k):
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(retention, "purge_expired_runs", _explode, raising=False)

    async def scenario():
        import archguard.db.store as store_module

        monkeypatch.setattr(store_module, "purge_expired_runs", _explode)
        return await retention.purge_expired_data()

    assert _run(scenario()) == 0
