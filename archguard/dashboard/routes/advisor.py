"""AI Advisor endpoints.

Both the analyze() path and the streaming ask endpoint now run on Gemini, so
this module no longer straddles two providers."""

import logging
from collections.abc import Generator
from typing import Any

from fastapi import Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from archguard.dashboard._auth import check_token
from archguard.dashboard._rate_limit import _llm_rate_limit
from archguard.dashboard.app import app
from archguard.llm.advisor import ArchitectureAdvisor

logger = logging.getLogger(__name__)


class AdvisorAskRequest(BaseModel):
    """Payload for the streaming advisor ask endpoint."""

    question: str = Field(..., max_length=2000, description="Architectural question (max 2000 chars)")


# -----------------------------------------------------------------------------
# Advisor endpoints
# -----------------------------------------------------------------------------


# Removed: session-based advisor sub-API had zero frontend callers (confirmed
# independently by two audit passes). The streaming `POST /advisor/ask` path
# is ArchGuard's one supported advisor interaction model. See CHANGELOG.


def _sse_event(text: str) -> str:
    """Encode *text* as one SSE event, safe for multi-line payloads.

    A ``data:`` field cannot carry a raw newline: the first one ends the field
    and a blank line ends the event. Emitting ``f"data: {chunk}\\n\\n"`` for a
    markdown chunk therefore truncated it at the first newline and left the
    remaining lines as bare text, which browsers discard because they are not
    ``data:`` fields -- silently deleting list items and table rows.

    The spec's encoding is one ``data:`` line per line of payload; the client
    rejoins them with a newline. A trailing newline in *text* is preserved as a
    final empty ``data:`` line so blank-line separated markdown survives.
    """
    return "".join(f"data: {line}\n" for line in text.split("\n")) + "\n"


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
async def advisor_ask_stream(body: AdvisorAskRequest, job_id: str | None = Query(None)) -> StreamingResponse:
    """Stream a Gemini response to an architectural question.

    Returns a text/event-stream (SSE) response where each line is a raw text
    chunk yielded by ArchitectureAdvisor.ask_stream().

    Requires GEMINI_API_KEY to be set; falls back to a single error chunk
    when the key is missing or the API call fails.
    """
    question = body.question.strip()
    if not question:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="question must not be empty",
        )

    # Ground the answer on *this* run only. The previous behaviour, when job_id
    # was absent, was audit.read_last_run() against the server's cwd log --
    # i.e. whichever repository anyone analysed most recently on this server.
    # An ungrounded answer is fine; an answer silently grounded on a stranger's
    # repository is not. Resolve strictly, and say so when there is nothing.
    latest: dict[str, Any] | None = None
    if job_id:
        from archguard.db import store
        from archguard.db.session import session_scope

        async with session_scope() as session:
            latest = await store.get_latest_run(session, job_id)

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

    # ask_stream() builds its own Gemini client, so no provider is needed here.
    advisor = ArchitectureAdvisor()

    def _sse_generator() -> Generator[str, None, None]:
        try:
            if ungrounded_notice:
                yield _sse_event(ungrounded_notice)
            for chunk in advisor.ask_stream(question=question, context=prompt_context):
                yield _sse_event(chunk)
        except Exception as exc:  # pragma: no cover
            logger.warning("advisor_ask_stream error: %s", exc)
            yield _sse_event(
                "An internal error occurred while streaming. "
                "Check server logs for details."
            )

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
