"""Job submission, status, and GitHub URL validation routes for Phase 2."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import asdict
from typing import Any

import httpx
from fastapi import BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from archguard.dashboard._auth import check_token
from archguard.dashboard._rate_limit import rate_limiter
from archguard.dashboard.app import app

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


@app.post(
    "/api/v1/jobs/validate",
    response_model=RepoMetadata,
    dependencies=[Depends(check_token), Depends(rate_limiter)],
    summary="Validate a GitHub URL and return repository metadata",
)
@app.post(
    "/api/jobs/validate",
    response_model=RepoMetadata,
    dependencies=[Depends(check_token), Depends(rate_limiter)],
    summary="Validate a GitHub URL and return repository metadata",
    deprecated=True,
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


@app.post(
    "/api/v1/jobs",
    status_code=202,
    dependencies=[Depends(check_token), Depends(rate_limiter)],
    summary="Submit a repository analysis job",
)
@app.post(
    "/api/jobs",
    status_code=202,
    dependencies=[Depends(check_token), Depends(rate_limiter)],
    summary="Submit a repository analysis job",
    deprecated=True,
)
async def submit_analysis_job(
    request: SubmitJobRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Validate the GitHub URL and enqueue a background analysis job.

    Returns immediately (HTTP 202) with a job_id.
    Poll GET /api/jobs/{job_id} or stream GET /api/jobs/{job_id}/stream.
    """
    from archguard.dashboard.job_manager import job_manager

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
    job = job_manager.create_job(github_url=safe_url)
    task = asyncio.create_task(job_manager.run_job(job))
    job_manager.track_task(job.id, task)

    from archguard.dashboard._cookie_auth import _issue_short_lived_stream_token
    stream_token = _issue_short_lived_stream_token(job.id)
    token_qs = f"?token={stream_token}" if stream_token else ""

    return {
        "job_id": job.id,
        "status": job.status,
        "message": "Analysis queued.",
        "validation_skipped_rate_limit": rate_limit_hit,
        "poll_url": f"/api/v1/jobs/{job.id}",
        "stream_url": f"/api/v1/jobs/{job.id}/stream{token_qs}",
    }


# --------------------------------------------------------------------------
# Job status
# --------------------------------------------------------------------------


@app.get(
    "/api/v1/jobs/{job_id}",
    dependencies=[Depends(check_token), Depends(rate_limiter)],
    summary="Get analysis job status and result",
)
@app.get(
    "/api/jobs/{job_id}",
    dependencies=[Depends(check_token), Depends(rate_limiter)],
    summary="Get analysis job status and result",
    deprecated=True,
)
async def get_job_status(job_id: str, request: Request) -> dict[str, Any]:
    """Return the current status, progress, and result of an analysis job."""
    from archguard.dashboard.job_manager import JobStatus, job_manager

    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    response: dict[str, Any] = {
        "job_id": job.id,
        "github_url": job.github_url,
        "status": job.status,
        "progress": job.progress_messages[-10:],   # last 10 progress messages
        "created_at": job.created_at.isoformat(),
    }

    if job.completed_at:
        response["completed_at"] = job.completed_at.isoformat()

    if job.status == JobStatus.COMPLETE and job.result is not None:
        response["result"] = job._cached_result_dict if job._cached_result_dict else asdict(job.result)

    if job.status == JobStatus.FAILED:
        response["error"] = job.error

    return response


# --------------------------------------------------------------------------
# Job list
# --------------------------------------------------------------------------


@app.get(
    "/api/v1/jobs",
    dependencies=[Depends(check_token), Depends(rate_limiter)],
    summary="List recent analysis jobs",
)
@app.get(
    "/api/jobs",
    dependencies=[Depends(check_token), Depends(rate_limiter)],
    summary="List recent analysis jobs",
    deprecated=True,
)
async def list_jobs() -> dict[str, Any]:
    """Return the most recent analysis jobs (up to 50)."""
    from archguard.dashboard.job_manager import job_manager

    return {
        "jobs": [
            {
                "job_id": j.id,
                "github_url": j.github_url,
                "status": j.status,
                "created_at": j.created_at.isoformat(),
                "health_score": j.result.health_score if j.result else None,
                "health_grade": j.result.health_grade if j.result else None,
            }
            for j in job_manager.list_jobs()
        ]
    }


# --------------------------------------------------------------------------
# SSE Progress Stream
# --------------------------------------------------------------------------


@app.get(
    "/api/v1/jobs/{job_id}/stream",
    response_class=StreamingResponse,
    dependencies=[Depends(rate_limiter)],
    summary="Stream analysis progress as Server-Sent Events",
)
@app.get(
    "/api/jobs/{job_id}/stream",
    response_class=StreamingResponse,
    dependencies=[Depends(rate_limiter)],
    summary="Stream analysis progress as Server-Sent Events",
    deprecated=True,
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

    from archguard.dashboard.job_manager import JobStatus, job_manager

    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    from collections.abc import AsyncGenerator
    async def event_generator() -> AsyncGenerator[str, None]:
        last_progress_idx = 0
        last_emitted_status: str | None = None

        while True:
            # Detect client disconnect
            if await request.is_disconnected():
                logger.info("[SSE] Client disconnected from job %s stream", job_id)
                break

            # Re-fetch job on each iteration (job object is mutable)
            current_job = job_manager.get_job(job_id)
            if current_job is None:
                break

            # Emit new progress messages since last emission
            new_messages = current_job.progress_messages[last_progress_idx:]
            for msg in new_messages:
                payload = json.dumps({"type": "progress", "message": msg})
                yield f"data: {payload}\n\n"
            last_progress_idx += len(new_messages)

            # Emit status only when it has changed
            current_status = str(current_job.status)
            if current_status != last_emitted_status:
                yield f"data: {json.dumps({'type': 'status', 'status': current_job.status})}\n\n"
                last_emitted_status = current_status

            # Terminal states: emit result or error, then close
            if current_job.status == JobStatus.COMPLETE:
                if current_job.result is not None:
                    result_payload = json.dumps({
                        "type": "result",
                        "result": current_job._cached_result_dict if current_job._cached_result_dict else asdict(current_job.result),
                    })
                    yield f"data: {result_payload}\n\n"
                yield 'data: {"type": "done"}\n\n'
                break

            if current_job.status == JobStatus.FAILED:
                error_payload = json.dumps({
                    "type": "error",
                    "error": current_job.error or "Unknown error",
                })
                yield f"data: {error_payload}\n\n"
                yield 'data: {"type": "done"}\n\n'
                break

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
@app.get(
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
