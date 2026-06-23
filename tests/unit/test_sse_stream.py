import json
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from archguard.dashboard.app import app
from archguard.dashboard.job_manager import AnalysisJob, JobStatus

@pytest.fixture
def client():
    return TestClient(app)

def test_stream_not_found(client):
    resp = client.get("/api/jobs/nonexistent/stream")
    assert resp.status_code == 404

def test_stream_complete_job(client):
    """A COMPLETE job should stream progress + result + done."""
    from archguard.dashboard.pipeline_adapter import AnalysisJobResult, LayerResult
    from datetime import datetime, timezone

    mock_result = AnalysisJobResult(
        job_id="test", repo_url="https://github.com/x/y",
        health_score=80.0, health_grade="B",
        composite_score=0.2, total_violations=1,
    )
    
    fake_job = AnalysisJob(
        id="test-stream",
        github_url="https://github.com/x/y",
        status=JobStatus.COMPLETE,
        progress_messages=["Cloned.", "Analysis done."],
        result=mock_result,
    )

    with patch("archguard.dashboard.job_manager.job_manager.get_job", return_value=fake_job):
        with client.stream("GET", "/api/jobs/test-stream/stream") as resp:
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

def test_stream_failed_job(client):
    """A FAILED job should stream error + done."""
    from datetime import datetime, timezone
    
    fake_job = AnalysisJob(
        id="test-fail",
        github_url="https://github.com/x/y",
        status=JobStatus.FAILED,
        error="Clone timed out",
    )
    
    with patch("archguard.dashboard.job_manager.job_manager.get_job", return_value=fake_job):
        with client.stream("GET", "/api/jobs/test-fail/stream") as resp:
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
