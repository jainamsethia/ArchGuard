"""Unit tests for the Advisor API endpoints (Phase 4 Step 14)."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from archguard.dashboard.app import app
from archguard.dashboard._sessions import SESSION_STORE
from archguard.llm.advisor import Recommendation

client = TestClient(app)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_sessions():
    """Ensure SESSION_STORE is clean before each test."""
    SESSION_STORE.clear()
    yield
    SESSION_STORE.clear()


@pytest.fixture()
def mock_empty_audit():
    with patch("archguard.dashboard.routes.advisor.AuditLogger") as cls:
        inst = MagicMock()
        inst.read_last_n_runs.return_value = []
        cls.return_value = inst
        yield inst


@pytest.fixture()
def mock_audit_with_runs():
    with patch("archguard.dashboard.routes.advisor.AuditLogger") as cls:
        inst = MagicMock()
        inst.read_last_n_runs.return_value = [
            {"score": 60.0, "band": "WARN", "violations": [], "metrics": {}},
            {"score": 80.0, "band": "PASS", "violations": [], "metrics": {}},
        ]
        cls.return_value = inst
        yield inst


def _mock_advisor(recs: list[Recommendation]):
    """Patch _build_advisor to return an advisor that emits *recs*."""
    mock_advisor = MagicMock()
    mock_advisor.analyze.return_value = recs
    return patch(
        "archguard.dashboard.routes.advisor._build_advisor", return_value=mock_advisor
    )


def _mock_provider(recs: list[Recommendation]):
    """Patch OpenAIAdvisorProvider inside advisor_message endpoint."""
    mock_prov = MagicMock()
    mock_prov.generate_recommendations.return_value = recs
    return patch(
        "archguard.dashboard.routes.advisor.OpenAIAdvisorProvider",
        return_value=mock_prov,
    )


# ---------------------------------------------------------------------------
# POST /api/advisor/session  —  session creation
# ---------------------------------------------------------------------------


def test_create_session_empty_history(mock_empty_audit, monkeypatch):
    """Session creation with no audit data returns empty recommendations."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import RATE_LIMITS

    RATE_LIMITS.clear()

    with _mock_advisor([]):
        resp = client.post("/api/advisor/session")

    assert resp.status_code == 200
    body = resp.json()
    assert "session_id" in body
    assert body["recommendations"] == []
    assert "healthy" in body["message"] or "enough audit data" in body["message"]

    # Session must be stored
    assert body["session_id"] in SESSION_STORE


def test_create_session_with_recommendations(mock_audit_with_runs, monkeypatch):
    """Session creation returns prioritised recommendations."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import RATE_LIMITS

    RATE_LIMITS.clear()

    recs = [
        Recommendation(
            "Fix cycles",
            "Remove circular imports",
            "critical",
            "Better build times",
            90,
        ),
        Recommendation("Add tests", "Increase coverage", "high", "Fewer bugs", 70),
    ]

    with _mock_advisor(recs):
        resp = client.post("/api/advisor/session")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["recommendations"]) == 2
    assert body["recommendations"][0]["title"] == "Fix cycles"
    assert body["recommendations"][0]["severity"] == "critical"
    assert "Fix cycles" in body["message"]


def test_create_session_history_seeded(mock_audit_with_runs, monkeypatch):
    """Newly created session has the initial assistant turn in its history."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import RATE_LIMITS

    RATE_LIMITS.clear()

    recs = [
        Recommendation("Fix cycles", "Remove circular imports", "high", "Impact", 80)
    ]

    with _mock_advisor(recs):
        resp = client.post("/api/advisor/session")

    session_id = resp.json()["session_id"]
    session = SESSION_STORE[session_id]
    assert len(session["history"]) == 1
    assert session["history"][0]["role"] == "assistant"


def test_create_session_provider_failure(mock_empty_audit, monkeypatch):
    """If advisor.analyze raises, session is still created with empty recs."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import RATE_LIMITS

    RATE_LIMITS.clear()

    failing_advisor = MagicMock()
    failing_advisor.analyze.side_effect = RuntimeError("LLM is down")

    with patch(
        "archguard.dashboard.routes.advisor._build_advisor",
        return_value=failing_advisor,
    ):
        resp = client.post("/api/advisor/session")

    assert resp.status_code == 200
    body = resp.json()
    assert body["recommendations"] == []


# ---------------------------------------------------------------------------
# POST /api/advisor/session/{session_id}/message  —  conversation
# ---------------------------------------------------------------------------


def _seed_session(session_id: str) -> None:
    """Insert a dummy session directly into SESSION_STORE."""
    import time

    SESSION_STORE[session_id] = {
        "_ts": time.time(),
        "created_at": "2024-01-01T00:00:00+00:00",
        "recommendations": [],
        "history": [{"role": "assistant", "content": "Initial summary."}],
    }


def test_message_continues_conversation(monkeypatch):
    """Follow-up message appends user + assistant turns to history."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import RATE_LIMITS

    RATE_LIMITS.clear()

    sid = "test-session-123"
    _seed_session(sid)

    reply_rec = Recommendation(
        "Refactor layers", "Move code between layers", "medium", "Cleaner deps", 60
    )

    with _mock_provider([reply_rec]):
        resp = client.post(
            f"/api/advisor/session/{sid}/message",
            json={"message": "How do I fix layer violations?"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "assistant"
    assert body["content"] == reply_rec.description

    # History should now have 3 turns: initial + user + assistant
    assert len(body["history"]) == 3
    assert body["history"][1]["role"] == "user"
    assert body["history"][1]["content"] == "How do I fix layer violations?"
    assert body["history"][2]["role"] == "assistant"


def test_message_session_not_found(monkeypatch):
    """POST message to unknown session returns 404."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import RATE_LIMITS

    RATE_LIMITS.clear()

    resp = client.post(
        "/api/advisor/session/nonexistent-id/message",
        json={"message": "Hello?"},
    )
    assert resp.status_code == 404


def test_message_provider_failure_returns_graceful_reply(monkeypatch):
    """When provider fails, a graceful error reply is returned (not 500)."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import RATE_LIMITS

    RATE_LIMITS.clear()

    sid = "session-fail"
    _seed_session(sid)

    failing_prov = MagicMock()
    failing_prov.generate_recommendations.side_effect = RuntimeError("API down")

    with patch(
        "archguard.dashboard.routes.advisor.OpenAIAdvisorProvider",
        return_value=failing_prov,
    ):
        resp = client.post(
            f"/api/advisor/session/{sid}/message",
            json={"message": "Analyse my code"},
        )

    assert resp.status_code == 200
    assert (
        "error" in resp.json()["content"].lower()
        or "try again" in resp.json()["content"].lower()
    )


def test_message_no_provider_recs_returns_graceful_reply(monkeypatch):
    """When provider returns no recs, a graceful reply is returned."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import RATE_LIMITS

    RATE_LIMITS.clear()

    sid = "session-empty-recs"
    _seed_session(sid)

    with _mock_provider([]):
        resp = client.post(
            f"/api/advisor/session/{sid}/message",
            json={"message": "What should I fix?"},
        )

    assert resp.status_code == 200
    assert "unable" in resp.json()["content"].lower()


# ---------------------------------------------------------------------------
# GET /api/advisor/session/{session_id}  —  session retrieval
# ---------------------------------------------------------------------------


def test_get_session_returns_history(monkeypatch):
    """GET on existing session returns full history + recommendations."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import RATE_LIMITS

    RATE_LIMITS.clear()

    import time

    sid = "get-session-test"
    SESSION_STORE[sid] = {
        "_ts": time.time(),
        "created_at": "2024-01-01T00:00:00+00:00",
        "recommendations": [
            {
                "title": "Fix deps",
                "description": "Clean up",
                "severity": "high",
                "expected_impact": "Faster builds",
                "priority_score": 75,
            }
        ],
        "history": [{"role": "assistant", "content": "Summary."}],
    }

    resp = client.get(f"/api/advisor/session/{sid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == sid
    assert len(body["history"]) == 1
    assert len(body["recommendations"]) == 1
    assert body["recommendations"][0]["title"] == "Fix deps"


def test_get_session_not_found(monkeypatch):
    """GET on missing session returns 404."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import RATE_LIMITS

    RATE_LIMITS.clear()

    resp = client.get("/api/advisor/session/does-not-exist")
    assert resp.status_code == 404


def test_get_session_respects_token_auth(monkeypatch):
    """GET on session returns 401 when token is required but missing."""
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "secret")

    resp = client.get("/api/advisor/session/some-session")
    assert resp.status_code == 401

def test_advisor_ask_question_too_long_returns_422() -> None:
    """
    Regression test for MED-004.
    Verifies: POST /api/v1/advisor/ask with a question field of 2001
    characters returns HTTP 422.
    """
    from fastapi.testclient import TestClient
    from archguard.dashboard.app import app
    from archguard.dashboard._rate_limit import _LLM_LIMITS
    _LLM_LIMITS.clear()
    client = TestClient(app)

    resp = client.post(
        "/api/v1/advisor/ask",
        json={"question": "x" * 2001},
    )
    assert resp.status_code == 422

def test_advisor_message_too_long_returns_422() -> None:
    """
    Regression test for MED-004.
    Verifies: a follow-up message over 2000 characters is rejected.
    """
    from fastapi.testclient import TestClient
    from archguard.dashboard.app import app
    from archguard.dashboard._rate_limit import _LLM_LIMITS
    _LLM_LIMITS.clear()
    client = TestClient(app)

    resp = client.post(
        "/api/advisor/session/some-session-id/message",
        json={"message": "x" * 2001},
    )
    assert resp.status_code == 422
