"""The SSE progress stream, reading the shared channel.

These used to mock ``job_manager.get_job`` and hand the endpoint an object held
in this process's memory. That is exactly the thing the worker split removed:
the analysis runs elsewhere now, so the stream reads a Redis-backed channel and
the tests publish to it the way the worker does.
"""

from __future__ import annotations

import json

import pytest

from archguard.worker import progress
from tests.db_fixtures import requires_postgres

pytestmark = requires_postgres


@pytest.fixture(autouse=True)
def clean_progress():
    progress.reset()
    yield
    progress.reset()


def _events(raw: str) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in raw.splitlines()
        if line.startswith("data: ")
    ]


def test_stream_not_found(auth_client):
    """A job this user does not own is not found, whoever else may own it."""
    resp = auth_client.get("/api/jobs/nonexistent/stream")
    assert resp.status_code == 404


def test_stream_complete_job(auth_client, seed_run):
    """Progress, then the result, then done."""
    job_id = seed_run()
    progress.publish(job_id, {"type": "progress", "message": "Cloned."})
    progress.publish(job_id, {"type": "progress", "message": "Analysis done."})
    progress.publish(job_id, {"type": "status", "status": "analysing"})
    progress.publish(
        job_id, {"type": "result", "result": {"health_score": 80.0}}
    )
    progress.publish(job_id, {"type": "status", "status": "complete"})

    with auth_client.stream("GET", f"/api/jobs/{job_id}/stream") as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    events = _events(body)
    kinds = [e["type"] for e in events]
    assert "progress" in kinds
    assert "status" in kinds
    assert "result" in kinds
    assert kinds[-1] == "done"

    result = next(e for e in events if e["type"] == "result")
    assert result["result"]["health_score"] == 80.0


def test_stream_failed_job(auth_client, seed_run):
    """A failed job streams the error, then done."""
    job_id = seed_run()
    progress.publish(job_id, {"type": "error", "error": "Clone timed out"})
    progress.publish(job_id, {"type": "status", "status": "failed"})

    with auth_client.stream("GET", f"/api/jobs/{job_id}/stream") as resp:
        body = "".join(resp.iter_text())

    events = _events(body)
    error = next(e for e in events if e["type"] == "error")
    assert error["error"] == "Clone timed out"
    assert events[-1]["type"] == "done"


def test_a_late_client_still_sees_the_whole_run(auth_client, seed_run):
    """Connecting after the analysis finished replays it from the start.

    The in-memory version could not do this at all once the job was evicted,
    and could not do it from a second replica at any point.
    """
    job_id = seed_run()
    for i in range(4):
        progress.publish(job_id, {"type": "progress", "message": f"step {i}"})
    progress.publish(job_id, {"type": "status", "status": "complete"})

    with auth_client.stream("GET", f"/api/jobs/{job_id}/stream") as resp:
        body = "".join(resp.iter_text())

    messages = [e["message"] for e in _events(body) if e["type"] == "progress"]
    assert messages == ["step 0", "step 1", "step 2", "step 3"]


def test_a_finished_job_with_no_progress_still_closes(auth_client, seed_run, monkeypatch):
    """The stored status ends the stream when the channel says nothing.

    A worker killed mid-job, or progress that has since expired, would
    otherwise leave the client waiting on a stream that never returns.
    """
    from archguard.dashboard.routes import jobs as jobs_route

    monkeypatch.setattr(jobs_route, "ARCHGUARD_STREAM_IDLE_LIMIT", 1)

    job_id = seed_run()  # seed_run leaves the job 'complete'
    with auth_client.stream("GET", f"/api/jobs/{job_id}/stream") as resp:
        body = "".join(resp.iter_text())

    events = _events(body)
    assert events[-1]["type"] == "done"
    assert any(e.get("status") == "complete" for e in events)
