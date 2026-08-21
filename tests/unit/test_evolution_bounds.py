"""What stops one caller from turning /evolution/analyze into a DoS. D4.

Each commit the endpoint examines creates a git worktree and runs a full
four-layer analysis, four threads wide. With a ceiling of 100 and a 50-per
-minute rate limit, one authenticated client could queue roughly five thousand
full analyses a minute -- with no timeout and no concurrency limit anywhere.

Three bounds close it, and they do different jobs: the ceiling caps one
request, the lock caps concurrent requests, and the timeout caps a single run
that will not finish. Each is tested separately because each fails separately.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from archguard.dashboard import _locks
from archguard.dashboard.routes import evolution as ev
from archguard.db.models import User
from tests.db_fixtures import requires_postgres

USER = User(id=1, github_id=0, login="test")
OTHER = User(id=2, github_id=1, login="other")


@pytest.fixture(autouse=True)
def clean_locks():
    _locks.reset_locks()
    yield
    _locks.reset_locks()


# --------------------------------------------------------------- ceiling


def test_the_request_ceiling_is_twenty():
    """100 was a hundred full analyses in one request."""
    assert ev.EvolutionAnalyzeRequest(max_commits=20).max_commits == 20
    with pytest.raises(ValueError):
        ev.EvolutionAnalyzeRequest(max_commits=21)
    with pytest.raises(ValueError):
        ev.EvolutionAnalyzeRequest(max_commits=100)


@requires_postgres
def test_the_ceiling_is_enforced_by_the_api(auth_client, seed_run):
    """422 from validation, before any worktree is created."""
    job_id = seed_run()
    resp = auth_client.post(
        f"/api/v1/evolution/analyze?job_id={job_id}", json={"max_commits": 100}
    )
    assert resp.status_code == 422


def test_zero_and_negative_are_rejected():
    for value in (0, -1):
        with pytest.raises(ValueError):
            ev.EvolutionAnalyzeRequest(max_commits=value)


# ------------------------------------------------------------------ lock


def test_a_second_concurrent_analysis_for_one_user_is_refused(monkeypatch, tmp_path):
    """409, not a second worktree.

    This is the bound that actually closes D4. Without it the ceiling only caps
    how much damage a single request does, and nothing stopped a caller opening
    fifty at once.
    """
    started = asyncio.Event()
    release = asyncio.Event()

    class _SlowTracker:
        def __init__(self, path):
            pass

        def analyze_history(self, max_commits=10):
            # Signal from the worker thread, then block until the test lets go.
            started.set()
            while not release.is_set():
                time.sleep(0.01)
            raise RuntimeError("stopped by the test")

    monkeypatch.setattr(ev, "get_target_path", lambda jid: tmp_path, raising=False)
    monkeypatch.setattr(
        "archguard.dashboard.app.get_target_path", lambda jid: tmp_path, raising=False
    )
    monkeypatch.setattr(
        "archguard.evolution.tracker.ArchitectureEvolutionTracker", _SlowTracker
    )

    async def _both():
        first = asyncio.create_task(
            ev.start_evolution(ev.EvolutionAnalyzeRequest(max_commits=1), None, USER)
        )
        await asyncio.wait_for(started.wait(), timeout=10)

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await ev.start_evolution(
                ev.EvolutionAnalyzeRequest(max_commits=1), None, USER
            )
        assert exc.value.status_code == 409
        assert "already running" in exc.value.detail

        release.set()
        await first

    asyncio.run(_both())


def test_one_users_lock_does_not_block_another(monkeypatch, tmp_path):
    """Per user, not global. One person's expensive operation is their own."""
    with _locks.single_flight("evolution", USER.id, ttl=60):
        # The same name for a different user must still be free.
        with _locks.single_flight("evolution", OTHER.id, ttl=60):
            pass


def test_the_lock_is_released_even_when_the_work_raises():
    """A failed analysis must not lock the user out of retrying it."""
    with pytest.raises(RuntimeError):
        with _locks.single_flight("evolution", USER.id, ttl=60):
            raise RuntimeError("boom")

    # Free again.
    with _locks.single_flight("evolution", USER.id, ttl=60):
        pass


def test_a_lock_expires(monkeypatch):
    """A process killed holding one must not lock the feature out forever."""
    monkeypatch.setattr("archguard.dashboard._locks.get_redis", lambda: None)
    _locks.reset_locks()

    assert _locks._acquire("lock:evolution:1", ttl=1) is True
    assert _locks._acquire("lock:evolution:1", ttl=1) is False
    time.sleep(1.1)
    assert _locks._acquire("lock:evolution:1", ttl=1) is True


def test_the_lock_fails_open_when_redis_is_down(monkeypatch):
    """Refusing every request because the lock store blipped is worse.

    What the lock prevents is expensive, not dangerous, so an unavailable lock
    store degrades to the old behaviour rather than to an outage of the feature.
    """
    import redis

    class _Broken:
        """Every call fails, which is what "Redis is down" actually looks like.

        A stub that only breaks the one method under test would let a second
        call elsewhere succeed, and then this would not be testing an outage.
        """

        def __getattr__(self, _name):
            def _raise(*a, **k):
                raise redis.ConnectionError("down")

            return _raise

    broken = _Broken()
    monkeypatch.setattr("archguard.dashboard._locks.get_redis", lambda: broken)
    assert _locks._acquire("lock:evolution:1", ttl=60) is True


# --------------------------------------------------------------- timeout


def test_an_analysis_that_will_not_finish_is_abandoned(monkeypatch, tmp_path):
    """504, rather than a request thread held indefinitely.

    There was no timeout at all: a repository whose commits are slow to analyse
    pinned a thread until the client gave up, and the work carried on after.
    """
    monkeypatch.setattr(ev, "EVOLUTION_TIMEOUT_SECONDS", 1)

    class _Forever:
        def __init__(self, path):
            pass

        def analyze_history(self, max_commits=10):
            time.sleep(30)

    monkeypatch.setattr(ev, "get_target_path", lambda jid: tmp_path, raising=False)
    monkeypatch.setattr(
        "archguard.dashboard.app.get_target_path", lambda jid: tmp_path, raising=False
    )
    monkeypatch.setattr(
        "archguard.evolution.tracker.ArchitectureEvolutionTracker", _Forever
    )

    from fastapi import HTTPException

    async def _go():
        with pytest.raises(HTTPException) as exc:
            await ev.start_evolution(
                ev.EvolutionAnalyzeRequest(max_commits=1), None, USER
            )
        assert exc.value.status_code == 504

    asyncio.run(_go())


def test_the_timeout_is_configurable():
    import os

    assert int(
        os.environ.get("ARCHGUARD_EVOLUTION_TIMEOUT", "300")
    ) == ev.EVOLUTION_TIMEOUT_SECONDS


# ------------------------------------------------------------- reporting


def test_a_refusal_is_not_reported_as_an_analysis_failure(monkeypatch, tmp_path):
    """409 and 504 must reach the client as themselves.

    The blanket handler at the end of this endpoint turns any exception into a
    200 carrying {"error": "analysis_failed"}. Left in its path, a client could
    not tell "you already have one running" from "the analysis broke" -- and
    would retry, which is the behaviour the lock exists to prevent.
    """
    monkeypatch.setattr(ev, "get_target_path", lambda jid: tmp_path, raising=False)
    monkeypatch.setattr(
        "archguard.dashboard.app.get_target_path", lambda jid: tmp_path, raising=False
    )

    from fastapi import HTTPException

    async def _go():
        with _locks.single_flight("evolution", USER.id, ttl=60):
            with pytest.raises(HTTPException) as exc:
                await ev.start_evolution(
                    ev.EvolutionAnalyzeRequest(max_commits=1), None, USER
                )
        assert exc.value.status_code == 409

    asyncio.run(_go())
