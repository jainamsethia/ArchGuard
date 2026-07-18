from fastapi.testclient import TestClient
from archguard.dashboard.app import app
from archguard.dashboard.routes.advisor import ArchitectureAdvisor
from archguard.audit.logger import AuditLogger
from archguard.config import AUDIT_LOG_FILENAME
from pathlib import Path
import json

def test_advisor_context_enriched(monkeypatch, tmp_path):
    # Setup dummy audit log
    audit_file = tmp_path / AUDIT_LOG_FILENAME
    audit = AuditLogger(audit_file)
    audit.log_run(
        repo_url="https://github.com/foo/bar",
        job_id="test-job-123",
        score=50,
        band="C",
        violations=[
            {"layer": "domain", "module": "auth.py", "message": "Domain leaks infra", "severity": "high"},
            {"layer": "api", "module": "server.py", "message": "God class", "severity": "medium"}
        ]
    )
    
    # Mock Path.cwd() to point to our tmp_path so AuditLogger inside advisor.py uses the dummy log
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "test-token")
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", "1")
    
    # We want to capture what context is sent to ask_stream
    captured_context = []
    def fake_ask_stream(self, question: str, context: str):
        captured_context.append(context)
        yield "This is a mock answer"

    monkeypatch.setattr(ArchitectureAdvisor, "ask_stream", fake_ask_stream)
    
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/advisor/ask?job_id=test-job-123",
        json={"question": "which files have the most problems?", "context": "Fallback context - Score: 50, Violations: 2"},
        headers={"Authorization": "Bearer test-token"}
    )
    
    assert resp.status_code == 200
    
    assert len(captured_context) == 1
    final_context = captured_context[0]
    
    # Assert real violation data is in the context sent to LLM
    assert "Active Violations:" in final_context
    assert "auth.py" in final_context
    assert "Domain leaks infra" in final_context
    assert "server.py" in final_context
    assert "Fallback context - Score: 50" in final_context
