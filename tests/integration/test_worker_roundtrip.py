"""Submit, analyse, persist, stream -- across the process boundary.

The point of the worker split is that the analysis no longer happens in the
process serving HTTP. That makes three things testable that were not:

* progress written by one process is readable by another;
* a job survives the web process forgetting about it;
* the queue, not an asyncio task, is what carries work.

Run against real PostgreSQL and real Redis. A queue proved only against an
in-memory stand-in proves nothing about the one production runs.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from archguard.worker import progress
from tests.db_fixtures import requires_postgres

pytestmark = requires_postgres

requires_redis = pytest.mark.skipif(
    not os.environ.get("REDIS_URL", "").strip(),
    reason="REDIS_URL is not set; see docs/DEVELOPMENT.md for local setup",
)


@pytest.fixture(autouse=True)
def clean_progress():
    progress.reset()
    yield
    progress.reset()


# ------------------------------------------------------- progress channel


@requires_redis
def test_progress_written_by_one_caller_is_readable_by_another(live_db):
    """The whole reason the channel exists.

    ``AnalysisJob.progress_messages`` was a list on an object in the web
    process's memory: unreadable from a worker, unreadable from a second
    replica, and gone on restart.
    """
    progress.publish("job-1", {"type": "progress", "message": "Cloning..."})
    progress.publish("job-1", {"type": "status", "status": "analysing"})

    events = progress.read("job-1")
    assert [e["type"] for e in events] == ["progress", "status"]
    assert events[0]["message"] == "Cloning..."


@requires_redis
def test_a_late_reader_sees_everything_from_the_start(live_db):
    """A client that opens the stream after the analysis started sees the lot.

    This is why the record is a list and the pub/sub channel is only a
    doorbell: a subscriber that missed the earlier messages would otherwise
    join mid-analysis with a blank log.
    """
    for i in range(5):
        progress.publish("job-2", {"type": "progress", "message": f"step {i}"})

    assert len(progress.read("job-2")) == 5


@requires_redis
def test_a_reader_can_resume_from_where_it_stopped(live_db):
    """The cursor the SSE generator keeps, so a reconnect does not repeat."""
    for i in range(3):
        progress.publish("job-3", {"type": "progress", "message": f"step {i}"})
    first = progress.read("job-3")

    progress.publish("job-3", {"type": "progress", "message": "step 3"})
    resumed = progress.read("job-3", start=len(first))

    assert [e["message"] for e in resumed] == ["step 3"]


@requires_redis
def test_progress_is_scoped_per_job(live_db):
    progress.publish("job-a", {"type": "progress", "message": "a"})
    progress.publish("job-b", {"type": "progress", "message": "b"})

    assert [e["message"] for e in progress.read("job-a")] == ["a"]
    assert [e["message"] for e in progress.read("job-b")] == ["b"]


@requires_redis
def test_a_runaway_job_cannot_fill_redis(live_db):
    """Trimmed to the last N. One looping analysis must not take the rest down."""
    for i in range(progress.MAX_MESSAGES + 100):
        progress.publish("job-loop", {"type": "progress", "message": str(i)})

    events = progress.read("job-loop")
    assert len(events) == progress.MAX_MESSAGES
    assert events[-1]["message"] == str(progress.MAX_MESSAGES + 99), (
        "the messages kept must be the recent ones"
    )


@requires_redis
def test_progress_expires(live_db):
    from archguard.redis_client import get_redis

    progress.publish("job-ttl", {"type": "progress", "message": "x"})
    ttl = get_redis().ttl("job:job-ttl:progress")
    assert 0 < ttl <= progress.PROGRESS_TTL_SECONDS


def test_the_local_fallback_is_bounded(monkeypatch):
    """Without Redis the in-process map must not grow without limit."""
    monkeypatch.setattr("archguard.worker.progress.get_redis", lambda: None)
    monkeypatch.setattr(progress, "_LOCAL_MAX_JOBS", 5)
    progress.reset()
    for i in range(30):
        progress.publish(f"job-{i}", {"type": "progress", "message": "x"})
    assert len(progress._LOCAL) <= 5


# --------------------------------------------------------------- the task


@requires_redis
def test_an_unknown_job_id_fails_without_raising(live_db):
    """A traceback escaping the task would be retried as though transient.

    Re-cloning a repository that cannot be analysed just burns the worker
    again, so the task records a failure rather than letting arq retry it.
    """
    from archguard.worker.tasks import analyse_repository

    result = asyncio.run(
        analyse_repository(None, "00000000-0000-4000-8000-000000000000")
    )
    assert result == "failed"


@requires_redis
def test_a_malformed_url_is_recorded_as_a_failed_job(live_db, test_user):
    """The job ends in a terminal state the dashboard can render.

    A job that raised and left the row at "queued" showed the user a spinner
    that never stopped.
    """
    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.worker.tasks import analyse_repository
    from tests.db_fixtures import _run

    async def _go() -> tuple[str, str | None]:
        async with session_scope() as session:
            job = await store.create_job(
                session, "https://github.com/not a real repo", user_id=test_user.id
            )
            job_id = job.id
        await analyse_repository(None, job_id)
        async with session_scope() as session:
            stored = await store.get_job(session, job_id, test_user.id)
            return stored.status, stored.error

    status, error = _run(_go())
    assert status == "failed"
    assert error, "a failed job must carry a message the page can show"
    # Composed here, never an arbitrary exception string: those carry server
    # filesystem paths and module structure into the browser.
    assert "Traceback" not in error


# ---------------------------------------------------------------- routing


def test_the_queue_is_used_when_redis_is_configured(monkeypatch):
    """One branch decides this, and it is the only one."""
    from archguard.worker import queue

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.delenv("ARCHGUARD_INLINE_ANALYSIS", raising=False)
    assert queue.queue_available() is True


def test_no_redis_means_the_analysis_runs_in_process(monkeypatch):
    """The development path. Not a deployment: the config check refuses it."""
    from archguard.worker import queue

    monkeypatch.delenv("REDIS_URL", raising=False)
    assert queue.queue_available() is False


def test_inline_can_be_forced(monkeypatch):
    """For attaching a debugger to an analysis in the web process."""
    from archguard.worker import queue

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("ARCHGUARD_INLINE_ANALYSIS", "1")
    assert queue.queue_available() is False


@requires_redis
def test_an_unreachable_queue_does_not_silently_run_inline(monkeypatch, live_db):
    """A configured-but-down queue must fail the submission.

    Falling back to in-process here is how a web dyno ends up quietly running
    the workload the split exists to move off it -- with no error, and no way
    to tell from the outside.
    """
    from archguard.worker import queue

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")  # nothing there

    async def _go():
        return await queue.enqueue_analysis("00000000-0000-4000-8000-000000000000")

    with pytest.raises(Exception):
        asyncio.run(_go())
