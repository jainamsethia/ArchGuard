"""AI Advisor session endpoints (OpenAI-backed) and the streaming ask endpoint
(Anthropic-backed) — two different LLM providers, kept together because both
serve the dashboard's single Advisor UI panel."""

import uuid
import time
import logging
from datetime import datetime, timezone
from typing import Any, Generator
from pydantic import BaseModel
from fastapi import Path as FastAPIPath, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from archguard.dashboard._state import (
    app,
    check_token,
    _llm_rate_limit,
    rate_limiter,
    SESSION_STORE,
    _SESSION_LOCK,
    _purge_expired_sessions,
    _build_advisor,
    get_audit_path,
)
from archguard.audit.logger import AuditLogger
from archguard.llm.openai_provider import OpenAIAdvisorProvider
from archguard.llm.advisor import ArchitectureAdvisor

# ─────────────────────────────────────────────────────────────────────────────
# Advisor session models
# ─────────────────────────────────────────────────────────────────────────────


class AdvisorMessageRequest(BaseModel):
    """Payload for a follow-up question in an existing advisor session."""

    message: str


class AdvisorRecommendationOut(BaseModel):
    title: str
    description: str
    severity: str
    expected_impact: str
    priority_score: int


class AdvisorSessionResponse(BaseModel):
    session_id: str
    created_at: str
    recommendations: list[AdvisorRecommendationOut]
    message: str


class AdvisorMessageResponse(BaseModel):
    session_id: str
    role: str  # "assistant"
    content: str
    history: list[dict[str, str]]


class AdvisorSessionHistoryResponse(BaseModel):
    session_id: str
    created_at: str
    history: list[dict[str, str]]
    recommendations: list[AdvisorRecommendationOut]


class AdvisorAskRequest(BaseModel):
    """Payload for the streaming advisor ask endpoint."""

    question: str
    context: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Advisor endpoints
# ─────────────────────────────────────────────────────────────────────────────


@app.post(
    "/api/advisor/session",
    dependencies=[Depends(check_token), Depends(_llm_rate_limit)],
)
def create_advisor_session(limit: int = Query(default=20, ge=1, le=500), job_id: str | None = None) -> Any:
    """Create a new advisor session by running analysis on recent audit data."""
    _purge_expired_sessions()

    audit = AuditLogger(get_audit_path(job_id))
    runs = audit.read_last_n_runs(n=limit)

    advisor = _build_advisor()
    try:
        recs = advisor.analyze(runs)
    except Exception as exc:
        logging.warning("Advisor analysis failed: %s", exc)
        recs = []

    session_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    # Seed conversation history with a system-level summary
    if recs:
        system_msg = (
            f"I have analysed {len(runs)} recent ArchGuard run(s) and found "
            f"{len(recs)} prioritised recommendation(s). The top recommendation is: "
            f"{recs[0].title}."
        )
    else:
        system_msg = (
            "I have analysed the ArchGuard history. "
            "No recommendations were generated — the codebase looks healthy, "
            "or there is not enough audit data yet."
        )

    recs_out = [
        AdvisorRecommendationOut(
            title=r.title,
            description=r.description,
            severity=r.severity,
            expected_impact=r.expected_impact,
            priority_score=r.priority_score,
        )
        for r in recs
    ]

    session: dict[str, Any] = {
        "_ts": time.time(),
        "created_at": created_at,
        "recommendations": [r.model_dump() for r in recs_out],
        "history": [{"role": "assistant", "content": system_msg}],
    }

    with _SESSION_LOCK:
        SESSION_STORE[session_id] = session

    return AdvisorSessionResponse(
        session_id=session_id,
        created_at=created_at,
        recommendations=recs_out,
        message=system_msg,
    )


@app.post(
    "/api/advisor/session/{session_id}/message",
    dependencies=[Depends(check_token), Depends(_llm_rate_limit)],
)
def advisor_message(
    session_id: str = FastAPIPath(..., min_length=1, max_length=64),
    body: AdvisorMessageRequest = ...,  # type: ignore[assignment]
) -> Any:
    """Send a follow-up question inside an existing advisor session."""
    with _SESSION_LOCK:
        session = SESSION_STORE.get(session_id)

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )

    # Build a textual conversation context for the provider
    history: list[dict[str, str]] = session["history"]
    user_msg = body.message.strip()
    if not user_msg:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Message must not be empty",
        )

    history_text = "\n".join(
        f"{m['role'].capitalize()}: {m['content']}" for m in history
    )
    follow_up_context = (
        f"{history_text}\nUser: {user_msg}\n"
        "Please answer the above question with actionable architectural advice."
    )

    provider = OpenAIAdvisorProvider()
    _ = ArchitectureAdvisor(provider)
    try:
        follow_up_recs = provider.generate_recommendations(follow_up_context)
        if follow_up_recs:
            reply = follow_up_recs[0].description
        else:
            reply = "I'm unable to generate advice for that question at this time."
    except Exception as exc:
        logging.warning("Advisor follow-up failed: %s", exc)
        reply = "Provider error — please try again later."

    new_history = history + [
        {"role": "user", "content": user_msg},
        {"role": "assistant", "content": reply},
    ]

    with _SESSION_LOCK:
        if session_id in SESSION_STORE:
            SESSION_STORE[session_id]["history"] = new_history
            SESSION_STORE[session_id]["_ts"] = time.time()

    return AdvisorMessageResponse(
        session_id=session_id,
        role="assistant",
        content=reply,
        history=new_history,
    )


@app.post(
    "/api/v1/advisor/ask", dependencies=[Depends(check_token), Depends(_llm_rate_limit)]
)
def advisor_ask_stream(body: AdvisorAskRequest) -> StreamingResponse:
    """Stream an Anthropic Claude response to an architectural question.

    Returns a text/event-stream (SSE) response where each line is a raw text
    chunk yielded by ArchitectureAdvisor.ask_stream().

    Requires ANTHROPIC_API_KEY to be set; falls back to a single error chunk
    when the key is missing or the Anthropic SDK is unavailable.
    """
    question = body.question.strip()
    if not question:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="question must not be empty",
        )

    advisor = ArchitectureAdvisor.__new__(ArchitectureAdvisor)
    # ask_stream() does not use self.provider — it talks to Anthropic directly.
    # We skip provider construction entirely to avoid coupling.

    def _sse_generator() -> Generator[str, None, None]:
        try:
            for chunk in advisor.ask_stream(question=question, context=body.context):
                # SSE format: each event is "data: <payload>\n\n"
                yield f"data: {chunk}\n\n"
        except Exception as exc:  # pragma: no cover
            logging.warning("advisor_ask_stream error: %s", exc)
            yield f"data: [error] {exc}\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get(
    "/api/advisor/session/{session_id}",
    dependencies=[Depends(check_token), Depends(rate_limiter)],
)
def get_advisor_session(
    session_id: str = FastAPIPath(..., min_length=1, max_length=64),
) -> Any:
    """Retrieve an existing advisor session including conversation history."""
    with _SESSION_LOCK:
        session = SESSION_STORE.get(session_id)

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )

    return AdvisorSessionHistoryResponse(
        session_id=session_id,
        created_at=session["created_at"],
        history=session["history"],
        recommendations=[
            AdvisorRecommendationOut(**r) for r in session["recommendations"]
        ],
    )
