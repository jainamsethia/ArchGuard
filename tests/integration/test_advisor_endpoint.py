import os
from fastapi.testclient import TestClient
from archguard.dashboard.app import app

client = TestClient(app)


def test_advisor_ask_no_data():
    """POST /api/advisor/session/{session_id}/message with valid data but no prior session."""
    # Assuming the API requires a valid token based on actual endpoint definition
    headers = {"Authorization": "Bearer test_token"}
    response = client.post(
        "/api/advisor/session/999/message", json={"message": "Help me"}, headers=headers
    )
    assert response.status_code == 404


def test_advisor_ask_requires_question():
    """POST /api/advisor/session/{session_id}/message without a message/question."""
    headers = {"Authorization": "Bearer test_token"}
    response = client.post("/api/advisor/session/999/message", json={}, headers=headers)
    assert response.status_code == 422


def test_remediation_no_data():
    """
    Deviation: The PDF refers to a remediation endpoint (e.g. /api/remediation or similar).
    Actual implementation does not have any dedicated /api/remediation endpoint.
    To satisfy the 'Do not invent routes' and 'Do not create knowingly failing tests' rules,
    we skip this test or assert 404 on an expected missing route to document the mismatch.
    """
    headers = {"Authorization": "Bearer test_token"}
    response = client.get("/api/remediation", headers=headers)
    assert response.status_code == 404


def test_deps_endpoint():
    """GET /api/v1/deps"""
    headers = {"Authorization": "Bearer test_token"}
    response = client.get("/api/v1/deps", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "score" in data
    # dependencies test might skip pip-audit if not installed or timeout
    assert "vulnerabilities" in data or "skip_reason" in data


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
    from archguard.dashboard._state import _LLM_LIMITS

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
    from archguard.dashboard._state import _LLM_LIMITS

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
    """Without ANTHROPIC_API_KEY, the stream yields a single SSE error chunk."""
    from archguard.dashboard._state import _LLM_LIMITS

    _LLM_LIMITS.clear()
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        response = client.post(
            "/api/v1/advisor/ask",
            json={"question": "How should I decompose this monolith?"},
        )
        assert response.status_code == 200
        body = response.text
        # SSE format: must contain at least one "data: " line
        assert "data: " in body
        # The fallback message references the missing key
        assert "ANTHROPIC_API_KEY" in body or "key" in body.lower()
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved


def test_advisor_ask_stream_empty_question_rejected():
    """POST /api/v1/advisor/ask with empty question must return 422."""
    from archguard.dashboard._state import _LLM_LIMITS

    _LLM_LIMITS.clear()
    response = client.post(
        "/api/v1/advisor/ask",
        json={"question": "   "},
    )
    assert response.status_code == 422


def test_advisor_ask_stream_missing_question_rejected():
    """POST /api/v1/advisor/ask with no question field must return 422."""
    from archguard.dashboard._state import _LLM_LIMITS

    _LLM_LIMITS.clear()
    response = client.post(
        "/api/v1/advisor/ask",
        json={},
    )
    assert response.status_code == 422
