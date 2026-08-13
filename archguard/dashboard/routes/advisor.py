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

    from archguard.dashboard.app import get_audit_path

    # Ground the answer on *this* run only. The previous behaviour, when job_id
    # was absent, was audit.read_last_run() against the server's cwd log --
    # i.e. whichever repository anyone analysed most recently on this server.
    # An ungrounded answer is fine; an answer silently grounded on a stranger's
    # repository is not. Resolve strictly, and say so when there is nothing.
    latest: dict[str, Any] | None = None
    if job_id:
        audit = AuditLogger(get_audit_path(job_id))
        latest = next(
            (
                r
                for r in reversed(audit.read_last_n_runs(n=100))
                if r.get("job_id") == job_id
            ),
            None,
        )

    ungrounded_notice = ""
    if latest is None:
        ungrounded_notice = (
            "No analysis run is selected, so this answer is general architectural "
            "guidance and is not based on your repository.\n\n"
        )
        prompt_context = (
            "You have NO analysis data for any repository. Answer only in general "
            "terms and state plainly that you cannot see the user's codebase. Do "
            "not invent module names, scores, or violations."
        )
    else:
        prompt_context = (
            _build_context_from_violations(latest["violations"][:10])
            if latest.get("violations")
            else "This run recorded no violations."
        )
        # A run built on guessed module boundaries must not be presented as
        # though its findings were measured.
        if latest.get("fallback_directory_heuristic"):
            ungrounded_notice = (
                "Note: this run's module boundaries were guessed from directory "
                "names, not measured from commit history.\n\n"
            )
            prompt_context = (
                "IMPORTANT CAVEAT: this analysis could not use the repository's "
                "co-change history, so module boundaries were guessed from top-level "
                "directory names rather than measured. Say so whenever the answer "
                "depends on those boundaries being correct.\n\n" + prompt_context
            )

    # The Advisor streams via Anthropic (ask_stream); the OpenAI provider is not
    # needed for this endpoint and may be omitted.
    advisor = ArchitectureAdvisor()

    def _sse_generator() -> Generator[str, None, None]:
        try:
            if ungrounded_notice:
                yield f"data: {ungrounded_notice}\n\n"
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
