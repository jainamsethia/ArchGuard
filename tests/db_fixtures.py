"""A live PostgreSQL behind the dashboard, for tests that read persisted data.

Every dashboard read endpoint queries PostgreSQL now, so a test that wants to
assert on run history has to put a run in a database. These fixtures do that
against the real server named by ``TEST_DATABASE_URL``, and skip -- loudly, with
a pointer to the setup docs -- when there is none. Nothing here fakes a
database: a query that only ever ran against a stub proves nothing about the
one production uses.

Imported by ``tests/conftest.py`` so the fixtures are available everywhere.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

requires_postgres = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set; see docs/DEVELOPMENT.md for local setup",
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _alembic(*args: str, url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": url},
        cwd=_REPO_ROOT,
        check=False,
    )


@pytest.fixture(scope="session")
def _schema_at_head() -> Iterator[str]:
    """Migrate the test database once for the whole session."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    result = _alembic("upgrade", "head", url=TEST_DATABASE_URL)
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stderr}"
    yield TEST_DATABASE_URL


async def _truncate() -> None:
    from sqlalchemy import text

    from archguard.db.session import session_scope

    async with session_scope() as session:
        await session.execute(
            text(
                "TRUNCATE users, repositories, jobs, runs, violations, "
                "suppressions, dependency_scans, file_hashes RESTART IDENTITY CASCADE"
            )
        )


def _run(coro: Any) -> Any:
    """Drive a coroutine from a test, async or not.

    Always on a worker thread with its own loop, because ``asyncio.run`` raises
    inside an ``@pytest.mark.asyncio`` test and half these fixtures are used by
    both kinds. Safe only because the engine runs unpooled under tests
    (``ARCHGUARD_DB_POOL_SIZE=0``), so no connection is carried between the loop
    this opens and whichever one the test itself is using.
    """
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


@pytest.fixture()
def live_db(_schema_at_head: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Point the application at the test database, empty before and after."""
    from archguard.db.session import dispose_engine

    monkeypatch.setenv("DATABASE_URL", _schema_at_head)
    monkeypatch.setenv("ARCHGUARD_DB_POOL_SIZE", "0")
    _run(dispose_engine())
    _run(_truncate())
    try:
        yield _schema_at_head
    finally:
        _run(_truncate())
        _run(dispose_engine())


@pytest.fixture()
def seed_run(live_db: str) -> Callable[..., str]:
    """Factory writing a completed job plus one analysis run, returning the job id.

    Keyword arguments map onto the payload shape ``persist_run`` consumes, so a
    test states only the fields it asserts on.
    """
    from archguard.db import store
    from archguard.db.session import session_scope

    def _seed(
        repo_url: str = "https://github.com/example/repo",
        job_id: str | None = None,
        **payload: Any,
    ) -> str:
        async def _write() -> str:
            async with session_scope() as session:
                from archguard.db.models import Job

                # An explicit id may name a job an earlier call already created:
                # more than one run under one job is how a test pins which run
                # ``/runs/latest`` picks.
                existing = (
                    await session.get(Job, job_id) if job_id is not None else None
                )
                if existing is not None:
                    jid = existing.id
                elif job_id is None:
                    jid = (await store.create_job(session, repo_url)).id
                else:
                    repo = await store.upsert_repository(session, repo_url)
                    job = Job(id=job_id, repository_id=repo.id, status="complete")
                    session.add(job)
                    await session.flush()
                    jid = job.id
                await store.set_job_status(session, jid, "complete")
                full: dict[str, Any] = {
                    "repo_url": repo_url,
                    "score": 80.0,
                    "band": "PASS",
                    "violations": [],
                    "module_scores": {},
                    "modules_analyzed": [],
                    **payload,
                }
                await store.persist_run(session, jid, full)
                return str(jid)

        return str(_run(_write()))

    return _seed


def new_job_id() -> str:
    """A syntactically valid job id that no test has seeded."""
    return str(uuid.uuid4())
