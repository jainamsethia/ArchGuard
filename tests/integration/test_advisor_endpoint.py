import os
from fastapi.testclient import TestClient
from archguard.dashboard.app import app

client = TestClient(app)


def test_advisor_session_route_removed():
    """Session-based advisor routes were removed in the API consolidation.
    The old /api/advisor/session/{id}/message path must return 404."""
    headers = {"Authorization": "Bearer test_token"}
    response = client.post(
        "/api/advisor/session/999/message", json={"message": "Help me"}, headers=headers
    )
    assert response.status_code == 404


def test_advisor_session_route_removed_no_body():
    """Session-based advisor route removed — also 404 without body."""
    headers = {"Authorization": "Bearer test_token"}
    response = client.post("/api/advisor/session/999/message", json={}, headers=headers)
    assert response.status_code == 404


def test_remediation_no_data():
    """
    Deviation: The PDF refers to a remediation endpoint (e.g. /api/remediation or similar).
    Actual implementation does not have any dedicated /api/remediation endpoint.
    To satisfy the 'Do not invent routes' and 'Do not create knowingly failing tests' rules,
    we skip this test or assert 404/405 on an expected missing route to document the mismatch.
    """
    headers = {"Authorization": "Bearer test_token"}
    response = client.get("/api/remediation", headers=headers)
    assert response.status_code == 404


def test_deps_endpoint_requires_job_id():
    """GET /api/v1/deps requires a job_id query parameter.

    The endpoint validates job_id at two levels:
    1. Query-parameter regex pattern (JobIdQuery) rejects malformed UUIDs.
    2. Explicit guard: if job_id is None, returns 400 with a user-facing
       message guiding the caller to submit a job first.

    This is the intended contract — deps analysis runs against a checked-out
    repo workspace, which is always tied to a specific analysis job.  There
    is no meaningful "deps status" without a job context.
    """
    headers = {"Authorization": "Bearer test_token"}
    response = client.get("/api/v1/deps", headers=headers)
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "No analysis selected" in data["detail"]


def test_deps_endpoint_success(monkeypatch):
    """GET /api/v1/deps?job_id=<uuid> returns 200 with dependency data.

    Providies a valid UUID, creates the matching workspace directory so
    get_target_path resolves it, and mocks analyze_dependencies at the
    subprocess level to avoid requiring pip-audit in the test environment.
    """
    import uuid
    import tempfile
    import json
    import shutil
    from pathlib import Path
    from unittest.mock import patch

    job_id = str(uuid.uuid4())
    workspace = Path(tempfile.gettempdir()) / f"archguard-{job_id}" / "repo"
    workspace.mkdir(parents=True, exist_ok=True)

    # Create a fake requirements.txt so _find_req_file returns something
    (workspace / "requirements.txt").write_text("requests==2.31.0\n")

    # Mock subprocess.run so _run_pip_audit returns a known JSON payload
    fake_audit_output = json.dumps({
        "dependencies": [
            {"name": "requests", "version": "2.31.0", "vulns": []},
        ]
    })

    def _fake_subprocess_run(*args, **kwargs):
        class FakeProc:
            stdout = fake_audit_output
            stderr = ""
            returncode = 0
        return FakeProc()

    monkeypatch.setattr("archguard.analysis.deps.subprocess.run", _fake_subprocess_run)

    try:
        headers = {"Authorization": "Bearer test_token"}
        response = client.get(f"/api/v1/deps?job_id={job_id}", headers=headers)

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text[:200]}"
        )
        data = response.json()
        assert "score" in data
        assert "vulnerable_packages" in data
        assert "scanned_packages" in data
        assert data["scanned_packages"] == 1  # one dep in our fake output
    finally:
        shutil.rmtree(Path(tempfile.gettempdir()) / f"archguard-{job_id}", ignore_errors=True)


def test_evolution_endpoint():
    """GET /api/evolution/summary"""
    headers = {"Authorization": "Bearer test_token"}
    response = client.get("/api/evolution/summary", headers=headers)
    assert response.status_code == 200
    data = response.json()
    # It should have a status or equivalent
    assert isinstance(data, dict)


# ─────────────────────────────────────────────────────────────────────────────
# New tests: POST /api/v1/advisor/ask  (Phase 3 Step 11 – streaming endpoint)
# ─────────────────────────────────────────────────────────────────────────────


def test_advisor_ask_stream_endpoint_exists():
    """POST /api/v1/advisor/ask must exist and return 200 (even without API key)."""
    # With no ANTHROPIC_API_KEY set, ask_stream() yields a single error chunk.
    from archguard.dashboard._rate_limit import _LLM_LIMITS

    _LLM_LIMITS.clear()
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        response = client.post(
            "/api/v1/advisor/ask",
            json={"question": "What are the top arch risks?"},
        )
        assert response.status_code == 200
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved


def test_advisor_ask_stream_content_type():
    """POST /api/v1/advisor/ask must return text/event-stream content type."""
    from archguard.dashboard._rate_limit import _LLM_LIMITS

    _LLM_LIMITS.clear()
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        response = client.post(
            "/api/v1/advisor/ask",
            json={"question": "Describe layered arch"},
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved


def test_advisor_ask_stream_no_key_yields_error_chunk():
    """Without ANTHROPIC_API_KEY, the stream yields a single SSE error chunk.

    Pins the provider story: the Advisor streams via Anthropic, so the
    missing-key message must name ANTHROPIC_API_KEY (not OPENAI_API_KEY).
    """
    from archguard.dashboard._rate_limit import _LLM_LIMITS

    _LLM_LIMITS.clear()
    saved_anthropic = os.environ.pop("ANTHROPIC_API_KEY", None)
    # This test pins the *missing key* message. CI sets ARCHGUARD_MOCK_LLM=1,
    # which makes ask_stream yield canned text and never mention the env var.
    saved_mock = os.environ.pop("ARCHGUARD_MOCK_LLM", None)
    try:
        response = client.post(
            "/api/v1/advisor/ask",
            json={"question": "How should I decompose this monolith?"},
        )
        assert response.status_code == 200
        body = response.text
        # SSE format: must contain at least one "data: " line
        assert "data: " in body
        # The fallback message must direct the user to the correct env var.
        assert "ANTHROPIC_API_KEY" in body
        assert "OPENAI_API_KEY" not in body
    finally:
        if saved_anthropic is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved_anthropic
        if saved_mock is not None:
            os.environ["ARCHGUARD_MOCK_LLM"] = saved_mock


def test_advisor_ask_stream_empty_question_rejected():
    """POST /api/v1/advisor/ask with empty question must return 422."""
    from archguard.dashboard._rate_limit import _LLM_LIMITS

    _LLM_LIMITS.clear()
    response = client.post(
        "/api/v1/advisor/ask",
        json={"question": "   "},
    )
    assert response.status_code == 422


def test_advisor_ask_stream_missing_question_rejected():
    """POST /api/v1/advisor/ask with no question field must return 422."""
    from archguard.dashboard._rate_limit import _LLM_LIMITS

    _LLM_LIMITS.clear()
    response = client.post(
        "/api/v1/advisor/ask",
        json={},
    )
    assert response.status_code == 422
