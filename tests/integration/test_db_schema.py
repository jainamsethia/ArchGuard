"""Schema and migration tests against a real PostgreSQL server.

Deliberately not mocked. A migration that has never been executed against
PostgreSQL is not a migration, it is a file -- and an unrunnable one is the
single most common cause of a failed production deploy. Everything here talks
to the database named by ``TEST_DATABASE_URL`` and is skipped, loudly, when
there is none.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import func, inspect, select

pytestmark = pytest.mark.integration

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

requires_postgres = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set; see docs/DEVELOPMENT.md for local setup",
)


def _alembic(*args: str, url: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        check=False,
    )


@pytest.fixture(scope="module")
def migrated_database() -> Iterator[str]:
    """A database at head, torn back down to base afterwards."""
    result = _alembic("upgrade", "head", url=TEST_DATABASE_URL)
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stderr}"
    try:
        yield TEST_DATABASE_URL
    finally:
        _alembic("downgrade", "base", url=TEST_DATABASE_URL)


@pytest_asyncio.fixture
async def session(migrated_database: str) -> AsyncIterator[object]:
    """A session on the migrated database, emptied afterwards."""
    os.environ["DATABASE_URL"] = migrated_database
    from archguard.db import session_scope
    from archguard.db.session import dispose_engine

    async with session_scope() as s:
        yield s

    from sqlalchemy import delete

    from archguard.db import Job, Repository, User

    async with session_scope() as s:
        # Jobs cascade to runs, violations and dependency scans.
        await s.execute(delete(Job))
        await s.execute(delete(Repository))
        await s.execute(delete(User))
    await dispose_engine()


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------


@requires_postgres
def test_migration_round_trips() -> None:
    """upgrade -> downgrade -> upgrade, the gate the plan sets for this task.

    A downgrade that does not actually drop what the upgrade created leaves the
    second upgrade failing on objects that already exist -- which is discovered
    during a rollback, at the worst possible moment.
    """
    up1 = _alembic("upgrade", "head", url=TEST_DATABASE_URL)
    assert up1.returncode == 0, up1.stderr

    down = _alembic("downgrade", "base", url=TEST_DATABASE_URL)
    assert down.returncode == 0, down.stderr

    up2 = _alembic("upgrade", "head", url=TEST_DATABASE_URL)
    assert up2.returncode == 0, up2.stderr


@requires_postgres
def test_migration_matches_the_models() -> None:
    """`alembic check` fails when a model has drifted from the migrations.

    Without this, adding a column to a model and forgetting the migration is
    invisible until the query fails in production.
    """
    _alembic("upgrade", "head", url=TEST_DATABASE_URL)
    result = _alembic("check", url=TEST_DATABASE_URL)
    assert result.returncode == 0, (
        "models have drifted from the migrations -- run "
        f"`alembic revision --autogenerate`:\n{result.stdout}\n{result.stderr}"
    )


@requires_postgres
@pytest.mark.asyncio
async def test_every_model_table_exists(session: object) -> None:
    from archguard.db import Base, get_engine

    async with get_engine().connect() as conn:
        present = set(
            await conn.run_sync(lambda c: inspect(c).get_table_names())
        )
    assert set(Base.metadata.tables) <= present, (
        f"missing: {set(Base.metadata.tables) - present}"
    )


# ---------------------------------------------------------------------------
# Behaviour the dashboard depends on
# ---------------------------------------------------------------------------


@requires_postgres
@pytest.mark.asyncio
async def test_jsonb_payloads_round_trip(session: object) -> None:
    """The analysis payload is stored as JSONB, not as a serialised string.

    A str column would work until something needed to query inside it.
    """
    from archguard.db import Job, Repository, Run, session_scope

    contract = {"version": "3.0", "profile": "ci", "modules": [{"name": "api"}]}
    edges = [{"from": "api", "to": "db"}]

    async with session_scope() as s:
        repo = Repository(owner="o", name="n", url="https://github.com/o/n.git")
        s.add(repo)
        await s.flush()
        job = Job(repository_id=repo.id, status="complete")
        s.add(job)
        await s.flush()
        s.add(
            Run(
                job_id=job.id,
                repository_id=repo.id,
                health_score=87.5,
                contract=contract,
                import_edges=edges,
                module_scores={"api": 90.0},
            )
        )

    async with session_scope() as s:
        run = (await s.execute(select(Run))).scalar_one()
        assert run.contract == contract
        assert run.import_edges == edges
        assert run.module_scores == {"api": 90.0}
        assert isinstance(run.contract, dict), "JSONB must decode to a dict"


@requires_postgres
@pytest.mark.asyncio
async def test_deleting_a_job_removes_its_runs_and_violations(session: object) -> None:
    """Cascades, not orphans. A run without its job is unreachable data that
    still counts toward every aggregate."""
    from archguard.db import Job, Repository, Run, Violation, session_scope

    async with session_scope() as s:
        repo = Repository(owner="o", name="n2", url="https://github.com/o/n2.git")
        s.add(repo)
        await s.flush()
        job = Job(repository_id=repo.id, status="complete")
        s.add(job)
        await s.flush()
        run = Run(job_id=job.id, repository_id=repo.id, health_score=50.0)
        s.add(run)
        await s.flush()
        s.add(Violation(run_id=run.id, layer=1, severity="high", message="m"))
        job_id = job.id

    async with session_scope() as s:
        job = await s.get(Job, job_id)
        await s.delete(job)

    async with session_scope() as s:
        assert (await s.execute(select(func.count()).select_from(Run))).scalar_one() == 0
        assert (
            await s.execute(select(func.count()).select_from(Violation))
        ).scalar_one() == 0


@requires_postgres
@pytest.mark.asyncio
async def test_repository_url_is_unique(session: object) -> None:
    """One row per repository, so runs from different jobs correlate.

    Duplicated repository rows are why per-repo history was impossible in the
    JSONL design: nothing tied two scans of the same project together.
    """
    from sqlalchemy.exc import IntegrityError

    from archguard.db import Repository, session_scope

    async with session_scope() as s:
        s.add(Repository(owner="o", name="dup", url="https://github.com/o/dup.git"))

    with pytest.raises(IntegrityError):
        async with session_scope() as s:
            s.add(Repository(owner="o", name="dup", url="https://github.com/o/dup.git"))


@requires_postgres
@pytest.mark.asyncio
async def test_job_ids_are_uuids_not_sequential(session: object) -> None:
    """Job ids appear in URLs handed to the browser. Sequential ids would let
    anyone enumerate other people's analyses."""
    import uuid

    from archguard.db import Job, Repository, session_scope

    async with session_scope() as s:
        repo = Repository(owner="o", name="n3", url="https://github.com/o/n3.git")
        s.add(repo)
        await s.flush()
        job = Job(repository_id=repo.id, status="queued")
        s.add(job)
        await s.flush()
        uuid.UUID(job.id)  # raises if not a UUID
        assert len(job.id) == 36


@requires_postgres
@pytest.mark.asyncio
async def test_file_hashes_are_keyed_by_repository_and_path(session: object) -> None:
    """The incremental-analysis cache. Keyed by repository so it outlives the
    throwaway clone each job creates -- the reason the CLI's repo-root JSON
    file was useless to the web application."""
    from archguard.db import FileHash, Repository, session_scope

    async with session_scope() as s:
        repo = Repository(owner="o", name="n4", url="https://github.com/o/n4.git")
        s.add(repo)
        await s.flush()
        s.add(FileHash(repository_id=repo.id, path="api/x.py", sha256="a" * 64))
        repo_id = repo.id

    async with session_scope() as s:
        got = await s.get(FileHash, {"repository_id": repo_id, "path": "api/x.py"})
        assert got is not None
        assert got.sha256 == "a" * 64
