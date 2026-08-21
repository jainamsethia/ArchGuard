import json
from unittest.mock import patch

from archguard.dashboard.job_manager import AnalysisJob, JobStatus
from tests.db_fixtures import requires_postgres

pytestmark = requires_postgres


def test_stream_not_found(auth_client):
    """A job this user does not own is not found, whoever else may own it."""
    resp = auth_client.get("/api/jobs/nonexistent/stream")
    assert resp.status_code == 404

def test_stream_complete_job(auth_client, seed_run):
    """A COMPLETE job should stream progress + result + done.

    The job is seeded for real, because the stream checks ownership against the
    database before it looks at the in-memory map -- the map records no owner,
    so serving from it first would hand a stranger's progress to anyone holding
    the id, and the id is in the browser URL.
    """

    from archguard.dashboard.pipeline_adapter import AnalysisJobResult

    job_id = seed_run()

    mock_result = AnalysisJobResult(
        job_id="test", repo_url="https://github.com/x/y",
        health_score=80.0, health_grade="B",
        composite_score=0.2, total_violations=1,
    )

    fake_job = AnalysisJob(
        id=job_id,
        github_url="https://github.com/x/y",
        status=JobStatus.COMPLETE,
        progress_messages=["Cloned.", "Analysis done."],
        result=mock_result,
    )

    with patch("archguard.dashboard.job_manager.job_manager.get_job", return_value=fake_job):
        with auth_client.stream("GET", f"/api/jobs/{job_id}/stream") as resp:
            assert resp.status_code == 200
            events = []
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
                if events and events[-1].get("type") == "done":
                    break

            event_types = [e["type"] for e in events]
            assert "progress" in event_types
            assert "status" in event_types
            assert "result" in event_types
            assert "done" in event_types

            result_event = next(e for e in events if e["type"] == "result")
            assert result_event["result"]["health_score"] == 80.0

def test_stream_failed_job(auth_client, seed_run):
    """A FAILED job should stream error + done."""

    job_id = seed_run()
    fake_job = AnalysisJob(
        id=job_id,
        github_url="https://github.com/x/y",
        status=JobStatus.FAILED,
        error="Clone timed out",
    )

    with patch("archguard.dashboard.job_manager.job_manager.get_job", return_value=fake_job):
        with auth_client.stream("GET", f"/api/jobs/{job_id}/stream") as resp:
            events = []
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
                if events and events[-1].get("type") == "done":
                    break

            event_types = [e["type"] for e in events]
            assert "error" in event_types
            assert "done" in event_types

            error_event = next(e for e in events if e["type"] == "error")
            assert "timed out" in error_event["error"]
