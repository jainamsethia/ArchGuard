import os
from fastapi.testclient import TestClient
from archguard.dashboard.app import app

client = TestClient(app)


def test_advisor_session_route_removed():
    """Session-based advisor routes were removed in the API consolidation.
    The old /api/advisor/session/{id}/message path must return 404 or 405."""
    headers = {"Authorization": "Bearer test_token"}
    response = client.post(
        "/api/advisor/session/999/message", json={"message": "Help me"}, headers=headers
    )
    # Note: static file catch-all at "/" returns 405 for POST on nonexistent routes
    assert response.status_code in (404, 405)


def test_advisor_session_route_removed_no_body():
    """Session-based advisor route removed — also 404/405 without body."""
    headers = {"Authorization": "Bearer test_token"}
    response = client.post("/api/advisor/session/999/message", json={}, headers=headers)
    assert response.status_code in (404, 405)


def test_remediation_no_data():
    """
    Deviation: The PDF refers to a remediation endpoint (e.g. /api/remediation or similar).
    Actual implementation does not have any dedicated /api/remediation endpoint.
    To satisfy the 'Do not invent routes' and 'Do not create knowingly failing tests' rules,
    we skip this test or assert 404/405 on an expected missing route to document the mismatch.
    """
    headers = {"Authorization": "Bearer test_token"}
    response = client.get("/api/remediation", headers=headers)
    assert response.status_code in (404, 405)


def test_deps_endpoint():
    """GET /api/v1/deps — without a job_id the endpoint returns 400."""
    headers = {"Authorization": "Bearer test_token"}
    response = client.get("/api/v1/deps", headers=headers)
    # The /api/v1/deps endpoint requires a job_id; without one it returns 400
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "No analysis selected" in data["detail"]


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
    """Without ANTHROPIC_API_KEY, the stream yields a single SSE error chunk."""
    from archguard.dashboard._rate_limit import _LLM_LIMITS

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
