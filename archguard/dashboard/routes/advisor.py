"""AI Advisor session endpoints (OpenAI-backed) and the streaming ask endpoint
(Anthropic-backed) - two different LLM providers, kept together because both
serve the dashboard's single Advisor UI panel."""

import logging
from typing import Any, Generator
from pydantic import BaseModel, Field
from fastapi import Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from archguard.dashboard.app import app
from archguard.dashboard._auth import check_token
from archguard.dashboard._rate_limit import _llm_rate_limit

from archguard.audit.logger import AuditLogger
from archguard.llm.advisor import ArchitectureAdvisor

def _message_for_reason(reason: str) -> str:
    if reason == "no_api_key":
        return "AI Advisor is not configured. Please set ANTHROPIC_API_KEY."
    elif reason == "api_error":
        return "AI Advisor is temporarily unavailable due to an API error."
    return f"AI Advisor is unavailable: {reason}"

MAX_HISTORY_TURNS = 20  # cap on conversation turns serialised into each LLM prompt

# -----------------------------------------------------------------------------
# Advisor session models
# -----------------------------------------------------------------------------


class AdvisorAskRequest(BaseModel):
    """Payload for the streaming advisor ask endpoint."""

    question: str = Field(..., max_length=2000, description="Architectural question (max 2000 chars)")
    context: str = Field("", max_length=10000, description="Optional context from analysis results (max 10000 chars)")


# -----------------------------------------------------------------------------
# Advisor endpoints
# -----------------------------------------------------------------------------


# Removed: session-based advisor sub-API had zero frontend callers (confirmed
# independently by two audit passes). The streaming `POST /advisor/ask` path
# is ArchGuard's one supported advisor interaction model. See CHANGELOG.


def _build_context_from_violations(violations: list[Any]) -> str:
    lines = ["Active Violations:"]
    for v in violations:
        lines.append(
            f"- [L{v.get('layer', '?')}] {v.get('module', 'Unknown')}: {v.get('message', '')} ({v.get('severity', 'low')})"
        )
    return "\n".join(lines)

@app.post(
    "/api/v1/advisor/ask", dependencies=[Depends(check_token), Depends(_llm_rate_limit)]
)
def advisor_ask_stream(body: AdvisorAskRequest, job_id: str | None = Query(None)) -> StreamingResponse:
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

    from archguard.config import AUDIT_LOG_FILENAME
    from pathlib import Path

    context = body.context or ""
    
    audit = AuditLogger(Path.cwd() / AUDIT_LOG_FILENAME)
    latest = audit.read_last_run() if not job_id else next(
        (r for r in audit.read_last_n_runs(n=100) if r.get("job_id") == job_id), None
    )
    if latest and latest.get("violations"):
        real_context = _build_context_from_violations(latest["violations"][:10])
        prompt_context = f"{real_context}\n\n{context}"  # server-grounded con
    else:
        prompt_context = context

    # The Advisor streams via Anthropic (ask_stream); the OpenAI provider is not
    # needed for this endpoint and may be omitted.
    advisor = ArchitectureAdvisor()

    def _sse_generator() -> Generator[str, None, None]:
        try:
            for chunk in advisor.ask_stream(question=question, context=prompt_context):
                # SSE format: each event is "data: <payload>\n\n"
                yield f"data: {chunk}\n\n"
        except Exception as exc:  # pragma: no cover
            logging.warning("advisor_ask_stream error: %s", exc)
            yield "data: An internal error occurred while streaming. Check server logs for details.\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
