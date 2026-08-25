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
import time
import uuid
import warnings
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


#: The WSL distro hosting PostgreSQL and Redis on a Windows developer machine.
WSL_DISTRO = os.environ.get("ARCHGUARD_WSL_DISTRO", "Ubuntu")


def _configured_hosts() -> list[tuple[str, str, int]]:
    """(service, host, port) for each service the environment points at."""
    from urllib.parse import urlparse

    out = []
    for name, env in (("PostgreSQL", "DATABASE_URL"), ("Redis", "REDIS_URL")):
        url = os.environ.get(env, "").strip()
        if not url:
            continue
        parsed = urlparse(url)
        if parsed.hostname and parsed.port:
            out.append((name, parsed.hostname, parsed.port))
    return out


def _hold_wsl_open() -> subprocess.Popen[bytes] | None:
    """Keep the WSL2 VM alive for the whole session, or return None.

    WSL2 shuts an idle VM down, and on a Windows developer machine this
    project's PostgreSQL and Redis live inside it -- so a stretch of tests that
    touch neither service for long enough takes both down, and everything after
    that fails with ConnectionRefusedError. Measured: the VM was gone after 100
    seconds idle, and `wsl -l -v` reported "Stopped" while `systemctl` inside
    it reported the services active, because the query itself cold-booted the
    VM to answer.

    `.wslconfig`'s vmIdleTimeout does not control it on WSL 2.5.10 -- the key
    is accepted and the VM still stops, at -1 and at a week in milliseconds.
    What does work is an attached session: with one open, the VM stayed Running
    and both ports stayed reachable across the same idle period.

    So the suite holds one for its own duration rather than asking the
    developer to remember to. A no-op anywhere that is not Windows, has no
    wsl.exe, or points its services somewhere other than loopback.
    """
    if sys.platform != "win32":
        return None
    hosts = _configured_hosts()
    if not hosts or not all(h in ("127.0.0.1", "localhost", "::1") for _, h, _ in hosts):
        return None

    wsl = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wsl.exe"
    if not wsl.exists():
        return None
    try:
        return subprocess.Popen(
            [str(wsl), "-d", WSL_DISTRO, "--", "sleep", "86400"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except OSError:
        # No such distro, or wsl refused. The health check below reports what
        # that costs; it is not this function's job to fail the run.
        return None


@pytest.fixture(scope="session", autouse=True)
def _services_reachable() -> Iterator[None]:
    """Hold the services up for the session, and say plainly if they are not.

    Without the health check an unreachable Redis is reported 843 times -- once
    per unit test, each paying the connection timeout first -- and an
    unreachable PostgreSQL surfaces as "alembic upgrade failed" inside a
    fixture, which reads like a broken migration rather than a service that is
    not running.

    A warning rather than a failure: running without services is legitimate,
    and the tests that need them skip on their own. The point is that the
    reason appears once, at the top, in words.
    """

    keeper = _hold_wsl_open()
    if keeper is not None:
        # Give the VM a moment to finish booting before probing it.
        for _ in range(20):
            if all(_reachable(h, p) for _, h, p in _configured_hosts()):
                break
            time.sleep(0.5)

    for name, host, port in _configured_hosts():
        if not _reachable(host, port):
            warnings.warn(
                f"{name} is configured at {host}:{port} but is not reachable. "
                f"Tests needing it will skip or fail. On Windows the services "
                f"run inside WSL2: check `wsl -l -v` shows {WSL_DISTRO} "
                f"Running. See docs/DEVELOPMENT.md.",
                stacklevel=1,
            )

    try:
        yield
    finally:
        if keeper is not None:
            keeper.terminate()


def _reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


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
                "suppressions, dependency_scans, file_hashes, watched_repositories "
                "RESTART IDENTITY CASCADE"
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


#: A fixed, obviously-not-secret key. Tests need signatures to verify, not to
#: resist anyone.
TEST_SESSION_SECRET = "0123456789abcdef" * 4


@pytest.fixture()
def live_db(_schema_at_head: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Point the application at the test database, empty before and after."""
    from archguard.dashboard import _sessions
    from archguard.db.session import dispose_engine

    monkeypatch.setenv("DATABASE_URL", _schema_at_head)
    monkeypatch.setenv("ARCHGUARD_DB_POOL_SIZE", "0")
    monkeypatch.setenv("SESSION_SECRET", TEST_SESSION_SECRET)
    _run(dispose_engine())
    _run(_truncate())
    _sessions.reset_sessions()
    try:
        yield _schema_at_head
    finally:
        _sessions.reset_sessions()
        _run(_truncate())
        _run(dispose_engine())


@pytest.fixture()
def test_user(live_db: str) -> Any:
    """A real account row.

    Every route resolves a user now, so a test that wants to read anything has
    to be somebody. A real row rather than a stub, so the same ``user_id``
    filters run as in production.
    """
    from archguard.db import store
    from archguard.db.session import session_scope

    async def _create() -> Any:
        async with session_scope() as session:
            user = await store.upsert_user(
                session, github_id=4242, login="test-user", avatar_url=None
            )
            # Detached from the session on purpose: callers pass it straight
            # into a route as the `user` argument, long after this closes.
            await session.refresh(user)
            session.expunge(user)
            return user

    return _run(_create())


@pytest.fixture()
def second_user(live_db: str) -> Any:
    """A second real account, for asserting that one user's data stays theirs.

    Isolation cannot be tested with one account: a query that ignores
    ``user_id`` passes every single-user test ever written.
    """
    from archguard.db import store
    from archguard.db.session import session_scope

    async def _create() -> Any:
        async with session_scope() as session:
            user = await store.upsert_user(
                session, github_id=9184, login="second-user", avatar_url=None
            )
            await session.refresh(user)
            session.expunge(user)
            return user

    return _run(_create())


@pytest.fixture()
def auth_client(test_user: Any) -> Any:
    """A TestClient carrying a session cookie for ``test_user``."""
    from fastapi.testclient import TestClient

    from archguard.dashboard import _sessions
    from archguard.dashboard.app import app

    client = TestClient(app)
    client.cookies.set(_sessions.COOKIE_NAME, _sessions.issue(test_user.id))
    return client


@pytest.fixture()
def seed_run(test_user: Any) -> Callable[..., str]:
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
                    jid = (
                        await store.create_job(
                            session, repo_url, user_id=test_user.id
                        )
                    ).id
                else:
                    repo = await store.upsert_repository(session, repo_url)
                    job = Job(
                        id=job_id,
                        repository_id=repo.id,
                        user_id=test_user.id,
                        status="complete",
                    )
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
