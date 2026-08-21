"""Job submission, status, and GitHub URL validation routes for Phase 2."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from archguard.dashboard._auth import check_token
from archguard.dashboard._identity import current_user
from archguard.dashboard._rate_limit import rate_limiter
from archguard.db.models import JobStatus, User

#: Mounted at /api/v1. The dependencies are here rather than on each
#: decorator: repeating them per route is how one ends up missing.
router = APIRouter(dependencies=[Depends(check_token), Depends(rate_limiter)])

#: Also /api/v1, but without check_token in the decorator -- see the note on
#: the stream route.
stream_router = APIRouter(dependencies=[Depends(rate_limiter)])

#: Unprefixed and unauthenticated: /health.
meta_router = APIRouter()

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"

#: Largest repository accepted for analysis, in kilobytes -- the unit the GitHub
#: API reports `size` in. Default 500 MB.
#:
#: This value was already being fetched on every submission and then thrown
#: away. Nothing bounded the size of a clone, and because workspaces are created
#: with keep_alive=True and the age sweep exempts every job the manager still
#: knows about, a few large repositories filled the host's disk.
#:
#: Set to 0 to disable the ceiling (self-hosted deployments with their own disk
#: budget).
MAX_REPO_SIZE_KB: int = int(os.environ.get("ARCHGUARD_MAX_REPO_SIZE_KB", "512000"))

#: Polls of 0.2s with no new progress before the stream re-checks the stored
#: job status. 150 is 30 seconds -- long enough that a slow analysis phase does
#: not trigger it, short enough that a client is not left hanging on a job whose
#: worker died.
ARCHGUARD_STREAM_IDLE_LIMIT: int = int(
    os.environ.get("ARCHGUARD_STREAM_IDLE_LIMIT", "150")
)


# --------------------------------------------------------------------------
# Pydantic models
# --------------------------------------------------------------------------


class RepoURLRequest(BaseModel):
    github_url: str


class SubmitJobRequest(BaseModel):
    github_url: str


class RepoMetadata(BaseModel):
    owner: str
    repo: str
    full_name: str
    description: str | None = None
    language: str | None = None
    stars: int
    default_branch: str
    size_kb: int
    is_private: bool
    clone_url: str


# --------------------------------------------------------------------------
# URL parser
# --------------------------------------------------------------------------


# GitHub account names are alphanumeric with single internal hyphens, max 39
# chars -- notably no dots. Repository names additionally allow dots and
# underscores. Both are anchored to a non-dot first character so that "." and
# ".." can never be produced: those are valid matches for a naive
# ``[A-Za-z0-9_.-]+`` and made ``/repos/{owner}/{repo}`` collapse to the GitHub
# API root once the client normalised the path.
_OWNER_RE = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_REPO_RE = r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,99}?"

_URL_PATTERNS = [
    re.compile(rf"^https?://github\.com/({_OWNER_RE})/({_REPO_RE})(?:\.git)?$"),
    re.compile(rf"^git@github\.com:({_OWNER_RE})/({_REPO_RE})(?:\.git)?$"),
]


def parse_github_url(url: str) -> tuple[str, str]:
    """Parse owner and repo name from a GitHub URL.

    Accepts:
        https://github.com/owner/repo
        https://github.com/owner/repo.git
        git@github.com:owner/repo.git

    Does NOT accept path suffixes like /tree/main or /../../etc/passwd.

    Returns:
        (owner, repo_name) - both without .git suffix

    Raises:
        ValueError: if the URL does not match any known format
    """
    url = url.strip().rstrip("/")
    for pattern in _URL_PATTERNS:
        m = pattern.match(url)
        if m:
            return m.group(1), m.group(2)
    raise ValueError(
        f"Cannot parse GitHub URL: {url!r}. "
        "Expected format: https://github.com/owner/repo"
    )


def build_safe_clone_url(owner: str, repo_name: str) -> str:
    """Reconstruct a safe clone URL from validated owner/repo parts.

    Always builds from parts - never passes raw user input to git.
    """
    return f"https://github.com/{owner}/{repo_name}.git"


# --------------------------------------------------------------------------
# Public GitHub metadata fetch (no token required for public repos)
# --------------------------------------------------------------------------


def _make_public_github_headers() -> dict[str, str]:
    """Build GitHub API headers, adding token if GITHUB_TOKEN is set."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ArchGuard/0.3.0",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class GitHubRateLimitError(Exception):
    """Carries the GitHub rate-limit reset epoch (unix seconds) if available."""

    def __init__(self, message: str, reset_epoch: float | None = None) -> None:
        super().__init__(message)
        self.reset_epoch = reset_epoch


def fetch_repo_metadata_public(owner: str, repo_name: str) -> dict[str, Any]:
    """Fetch repository metadata from the GitHub API.

    Works without GITHUB_TOKEN for public repos (60 req/hr unauthenticated).
    With GITHUB_TOKEN: 5000 req/hr.

    Raises:
        ValueError: repo not found, private without token, or rate limited
        RuntimeError: unexpected GitHub API error
    """
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=_make_public_github_headers())
    except httpx.RequestError as exc:
        raise RuntimeError(f"Network error reaching GitHub API: {exc}") from exc

    if resp.status_code == 404:
        raise ValueError(
            f"Repository {owner}/{repo_name} not found or is private. "
            "Check the URL and ensure the repository is public."
        )

    if resp.status_code == 403:
        remaining = resp.headers.get("X-RateLimit-Remaining", "unknown")
        reset_epoch: float | None = None
        reset_header = resp.headers.get("X-RateLimit-Reset")
        if reset_header:
            try:
                reset_epoch = float(reset_header)
            except ValueError:
                reset_epoch = None
        raise GitHubRateLimitError(
            f"GitHub API rate limit exceeded (remaining: {remaining}). "
            "Set the GITHUB_TOKEN environment variable to increase limits to 5000 req/hr.",
            reset_epoch=reset_epoch,
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"GitHub API returned unexpected status {resp.status_code}: "
            f"{resp.text[:200]}"
        )

    return dict(resp.json())


# --------------------------------------------------------------------------
# Endpoint: POST /api/jobs/validate
# --------------------------------------------------------------------------


@router.post(
    "/jobs/validate",
    response_model=RepoMetadata,
    summary="Validate a GitHub URL and return repository metadata",
)
async def validate_repo_url(request: RepoURLRequest) -> Any:
    """Parse and validate a GitHub repository URL.

    Returns repository metadata if the repo exists and is public.
    Returns 422 for malformed URLs, 404 for non-existent repos,
    429 for rate-limited responses from GitHub.
    """
    try:
        owner, repo_name = parse_github_url(request.github_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        data = await asyncio.to_thread(fetch_repo_metadata_public, owner, repo_name)
    except GitHubRateLimitError as exc:
        # Surface the GitHub rate-limit reset so the UI can tell the user how
        # long to wait, rather than a hardcoded "60 seconds" guess.
        reset = exc.reset_epoch if getattr(exc, "reset_epoch", None) else None
        retry_after = max(1, int(reset - time.time())) if reset else 60
        resp = JSONResponse(
            status_code=429,
            content={"detail": str(exc), "retry_after": retry_after},
        )
        resp.headers["Retry-After"] = str(retry_after)
        return resp
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        logger.exception("GitHub API error fetching repo metadata: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach GitHub API. Check your network connection.")

    return RepoMetadata(
        owner=owner,
        repo=data["name"],
        full_name=data["full_name"],
        description=data.get("description"),
        language=data.get("language"),
        stars=data["stargazers_count"],
        default_branch=data["default_branch"],
        size_kb=data["size"],
        is_private=data["private"],
        clone_url=data["clone_url"],
    )


# --------------------------------------------------------------------------
# Job submission
# --------------------------------------------------------------------------


def _reject_if_too_large(
    metadata: dict[str, Any], owner: str, repo_name: str
) -> None:
    """Refuse a repository whose clone would not fit the disk budget.

    A missing or non-numeric ``size`` is treated as unknown and allowed
    through. Refusing on a payload we failed to parse would turn an upstream
    GitHub API change into an outage, and the clone timeout remains a backstop.
    """
    if MAX_REPO_SIZE_KB <= 0:
        return

    raw_size = metadata.get("size")
    try:
        size_kb = int(raw_size)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        logger.warning(
            "GitHub reported a non-numeric size %r for %s/%s; size limit not applied",
            raw_size, owner, repo_name,
        )
        return

    if size_kb <= MAX_REPO_SIZE_KB:
        return

    logger.info(
        "Rejected %s/%s: %d KB exceeds the %d KB limit",
        owner, repo_name, size_kb, MAX_REPO_SIZE_KB,
    )
    raise HTTPException(
        status_code=413,
        detail=(
            f"Repository {owner}/{repo_name} is {size_kb / 1024:.0f} MB, which "
            f"exceeds the {MAX_REPO_SIZE_KB / 1024:.0f} MB limit for analysis. "
            "Analyse a smaller repository, or raise ARCHGUARD_MAX_REPO_SIZE_KB "
            "if you host this instance."
        ),
    )


@router.post(
    "/jobs",
    status_code=202,
    summary="Submit a repository analysis job",
)
async def submit_analysis_job(
    request: SubmitJobRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """Validate the GitHub URL and enqueue a background analysis job.

    Returns immediately (HTTP 202) with a job_id.
    Poll GET /api/jobs/{job_id} or stream GET /api/jobs/{job_id}/stream.

    The job records who submitted it. That single column is what every read
    downstream filters on, so ownership is established here or nowhere.
    """

    # Validate URL format before queuing
    try:
        owner, repo_name = parse_github_url(request.github_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Validate that the repository actually exists before queuing a clone job.
    # This prevents wasting the semaphore slot on a 120s git clone timeout.
    # fetch_repo_metadata_public is a synchronous, blocking httpx.Client call
    # (confirmed: it uses `with httpx.Client(timeout=10.0) as client:`, not
    # httpx.AsyncClient) - it must be offloaded to a thread to avoid blocking
    # the FastAPI event loop for up to 10 seconds.
    rate_limit_hit = False
    metadata: dict[str, Any] | None = None
    try:
        metadata = await asyncio.to_thread(fetch_repo_metadata_public, owner, repo_name)
    except GitHubRateLimitError:
        # Rate limit hit - allow the job through rather than blocking the user.
        # The clone will reveal the real state; this is acceptable degraded behaviour.
        rate_limit_hit = True
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        logger.exception("GitHub API error fetching repo metadata: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach GitHub API. Check your network connection.")

    if metadata is not None:
        _reject_if_too_large(metadata, owner, repo_name)

    # Use the safe reconstructed URL (CRIT-001 fix) rather than the raw input
    safe_url = build_safe_clone_url(owner, repo_name)
    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.worker.queue import enqueue_analysis

    async with session_scope() as session:
        db_job = await store.create_job(session, safe_url, user_id=user.id)
        job_id = db_job.id

    try:
        execution = await enqueue_analysis(job_id)
    except Exception:
        # The job row exists but nothing will ever run it. Say so, rather than
        # returning 202 for an analysis that will sit at "queued" forever.
        async with session_scope() as session:
            await store.set_job_status(
                session,
                job_id,
                "failed",
                error="Could not queue the analysis. Try again shortly.",
            )
        raise HTTPException(
            status_code=503,
            detail="The analysis queue is unavailable. Try again shortly.",
        )

    from archguard.dashboard._cookie_auth import _issue_short_lived_stream_token
    stream_token = _issue_short_lived_stream_token(job_id)
    token_qs = f"?token={stream_token}" if stream_token else ""

    return {
        "job_id": job_id,
        "status": "queued",
        "message": "Analysis queued.",
        # Which path ran it. An operator can tell from one request whether the
        # instance they are looking at has a worker behind it, or is quietly
        # analysing in the web process.
        "execution": execution,
        "validation_skipped_rate_limit": rate_limit_hit,
        "poll_url": f"/api/v1/jobs/{job_id}",
        "stream_url": f"/api/v1/jobs/{job_id}/stream{token_qs}",
    }


# --------------------------------------------------------------------------
# Job status
# --------------------------------------------------------------------------


@router.get(
    "/jobs/{job_id}",
    summary="Get analysis job status and result",
)
async def get_job_status(
    job_id: str, request: Request, user: User = Depends(current_user)
) -> dict[str, Any]:
    """Return the current status, progress, and result of an analysis job.

    Ownership is settled against the database before the in-memory map is
    consulted at all. The map records no owner, so answering from it first
    would serve a stranger's job to anyone who guessed its id -- and a job id
    appears in the browser URL, so they are not hard to come by.
    """
    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.worker import progress

    async with session_scope() as session:
        job = await store.get_job(session, job_id, user.id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
        repo_url = await store.get_job_repo_url(session, job_id, user.id)
        status, error, created_at, completed_at = (
            job.status,
            job.error,
            job.created_at,
            job.completed_at,
        )

    messages = [
        e["message"]
        for e in progress.read(job_id)
        if e.get("type") == "progress" and e.get("message")
    ]

    response: dict[str, Any] = {
        "job_id": job_id,
        "github_url": repo_url,
        "status": status,
        "progress": messages[-10:],
        "created_at": created_at.isoformat(),
    }
    if completed_at:
        response["completed_at"] = completed_at.isoformat()

    if status == JobStatus.COMPLETE.value:
        # The run is the durable record; the in-memory result object it used to
        # read is gone, along with the restart that erased it.
        async with session_scope() as session:
            run = await store.get_latest_run(session, job_id, user.id)
        if run is not None:
            response["result"] = {
                "job_id": job_id,
                "repo_url": run.get("repo_url"),
                "health_score": run.get("score"),
                "health_grade": run.get("grade"),
                "total_violations": len(run.get("violations") or []),
                "skipped": run.get("skipped"),
                "skip_reason": run.get("skip_reason"),
                "modules_analyzed": run.get("modules_analyzed") or [],
            }

    if status == JobStatus.FAILED.value:
        response["error"] = error

    return response


# --------------------------------------------------------------------------
# Job list
# --------------------------------------------------------------------------


@router.get(
    "/jobs",
    summary="List recent analysis jobs",
)
async def list_jobs(user: User = Depends(current_user)) -> dict[str, Any]:
    """This user's most recent analysis jobs (up to 50).

    From the database, not the in-memory map: the map holds only what this
    process happens to have run since it started, and it holds every user's.
    Together those made this endpoint both incomplete and the enumeration
    primitive for D1 -- it returned every job id the instance had ever issued.
    """
    from archguard.db import store
    from archguard.db.session import session_scope

    async with session_scope() as session:
        return {"jobs": await store.list_jobs(session, user.id)}


# --------------------------------------------------------------------------
# SSE Progress Stream
# --------------------------------------------------------------------------


# On the stream router, not the main one: check_token is taken as a signature
# dependency below so the `token` query parameter stays in the OpenAPI schema
# for EventSource clients, and listing rate_limiter twice charged every stream
# connection two requests against the caller's own budget.
@stream_router.get(
    "/jobs/{job_id}/stream",
    response_class=StreamingResponse,
    summary="Stream analysis progress as Server-Sent Events",
)
async def stream_job_progress(
    job_id: str,
    request: Request,
    token: str | None = Query(None),
    # check_token is a signature dependency rather than a decorator one only so
    # the `token` query parameter stays in the OpenAPI schema for EventSource
    # clients. rate_limiter is *not* repeated here: it is already in the
    # decorator's `dependencies=`, and listing it twice charged every stream
    # connection two requests against the caller's own budget.
    _auth: None = Depends(check_token),
    user: User = Depends(current_user),
) -> StreamingResponse:
    """Stream real-time analysis progress for a job.

    Events emitted:
      {"type": "progress", "message": "..."}   - status messages from the pipeline
      {"type": "status",   "status": "..."}    - current JobStatus string
      {"type": "result",   "result": {...}}     - final AnalysisJobResult (on COMPLETE)
      {"type": "error",    "error": "..."}      - error string (on FAILED)
      {"type": "done"}                          - stream end sentinel

    The stream ends automatically when the job reaches COMPLETE or FAILED status.
    If the client disconnects, the stream stops but the background job continues.
    """

    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.worker import progress

    async with session_scope() as session:
        if await store.get_job(session, job_id, user.id) is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    from collections.abc import AsyncGenerator
    async def event_generator() -> AsyncGenerator[str, None]:
        """Replay a job's progress, then follow it.

        Reads from the shared progress channel rather than an object in this
        process's memory. Three things follow from that, and all three were
        broken before: a client that connects late sees everything from the
        start rather than only what arrives after it; a second web replica can
        stream a job the first one queued; and the analysis can run in a worker
        at all.
        """
        cursor = 0
        last_status: str | None = None
        idle_polls = 0
        # A finished job stops producing events, so the stream needs some other
        # reason to close. The terminal status is that reason; this bounds the
        # case where it never arrives -- a worker killed mid-job leaves a row
        # at "analysing" forever, and a stream that waits for it never returns.
        max_idle_polls = int(ARCHGUARD_STREAM_IDLE_LIMIT)

        while True:
            if await request.is_disconnected():
                logger.info("[SSE] Client disconnected from job %s stream", job_id)
                break

            events = progress.read(job_id, cursor)
            cursor += len(events)

            for event in events:
                kind = event.get("type")
                if kind == "status":
                    last_status = event.get("status")
                yield f"data: {json.dumps(event, default=str)}\n\n"

            if events:
                idle_polls = 0
            else:
                idle_polls += 1

            if last_status in ("complete", "failed"):
                yield 'data: {"type": "done"}\n\n'
                break

            if idle_polls >= max_idle_polls:
                # Fall back to the stored row: the job may have finished in a
                # worker whose progress has since expired, or in a process that
                # died before publishing a terminal event.
                async with session_scope() as session:
                    stored = await store.get_job(session, job_id, user.id)
                status = stored.status if stored else None
                if status in ("complete", "failed"):
                    yield f"data: {json.dumps({'type': 'status', 'status': status})}\n\n"
                    if status == "failed" and stored is not None and stored.error:
                        yield f"data: {json.dumps({'type': 'error', 'error': stored.error})}\n\n"
                    yield 'data: {"type": "done"}\n\n'
                    break
                idle_polls = 0

            await asyncio.sleep(0.2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",      # disable nginx buffering
            "Connection": "keep-alive",
        },
    )

# ----------------------------------------------------------------------------
# Health Check
# ----------------------------------------------------------------------------
# No prefix and no auth: platform health checks are unauthenticated by
# definition, and pointing one at /api/v1 would make liveness depend on the
# database being reachable. /ready is the check that should (P2-2).
@meta_router.get(
    "/health",
    summary="Application health check",
    tags=["meta"],
)
async def health_check() -> dict[str, Any]:
    """Return application health status.
    Used by Docker Compose healthcheck, Railway, and Render.
    Always returns HTTP 200 if the application is running.
    """
    import os
    import time

    from archguard.dashboard.app import _APP_START_TIME, _installed_version

    return {
        "status": "ok",
        "version": _installed_version(),
        "environment": os.environ.get("ENVIRONMENT", "development"),
        "uptime_seconds": round(time.time() - _APP_START_TIME),
    }
