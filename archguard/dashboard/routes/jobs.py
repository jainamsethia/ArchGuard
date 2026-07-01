"""Job submission, status, and GitHub URL validation routes for Phase 2."""

from __future__ import annotations

import logging
import os
import re
from typing import Any
import importlib.metadata

import httpx
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from archguard.dashboard.app import app
from archguard.dashboard._auth import check_token
from archguard.dashboard._rate_limit import rate_limiter
from dataclasses import asdict
from fastapi import BackgroundTasks, Request
from fastapi.responses import StreamingResponse
import asyncio
import json

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


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


def parse_github_url(url: str) -> tuple[str, str]:
    """Parse owner and repo name from a GitHub URL.

    Accepts:
        https://github.com/owner/repo
        https://github.com/owner/repo.git
        git@github.com:owner/repo.git

    Does NOT accept path suffixes like /tree/main or /../../etc/passwd.
    Those were previously accepted by a greedy (?:/.*)? suffix and are now
    rejected to eliminate URL path traversal.

    Returns:
        (owner, repo_name) — both without .git suffix

    Raises:
        ValueError: if the URL does not match any known format
    """
    patterns = [
        r"^https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?$",
        r"^git@github\.com:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?$",
    ]
    url = url.strip().rstrip("/")
    for pattern in patterns:
        m = re.match(pattern, url)
        if m:
            owner, repo_name = m.group(1), m.group(2)
            return owner, repo_name
    raise ValueError(
        f"Cannot parse GitHub URL: {url!r}. "
        "Expected format: https://github.com/owner/repo"
    )


def build_safe_clone_url(owner: str, repo_name: str) -> str:
    """Reconstruct a safe clone URL from validated owner/repo parts.

    Always builds from parts — never passes raw user input to git.
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
    pass


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
        raise GitHubRateLimitError(
            f"GitHub API rate limit exceeded (remaining: {remaining}). "
            "Set the GITHUB_TOKEN environment variable to increase limits to 5000 req/hr."
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
    "/api/jobs/validate",
    response_model=RepoMetadata,
    dependencies=[Depends(check_token), Depends(rate_limiter)],
    summary="Validate a GitHub URL and return repository metadata",
)
async def validate_repo_url(request: RepoURLRequest) -> RepoMetadata:
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
        data = fetch_repo_metadata_public(owner, repo_name)
    except GitHubRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")

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


@app.post(
    "/api/jobs",
    status_code=202,
    dependencies=[Depends(check_token), Depends(rate_limiter)],
    summary="Submit a repository analysis job",
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
    # httpx.AsyncClient) — it must be wrapped in run_in_executor to avoid
    # blocking the FastAPI event loop for up to 10 seconds.
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None, fetch_repo_metadata_public, owner, repo_name
        )
    except GitHubRateLimitError:
        # Rate limit hit — allow the job through rather than blocking the user.
        # The clone will reveal the real state; this is acceptable degraded behaviour.
        pass
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")

    # Use the safe reconstructed URL (CRIT-001 fix) rather than the raw input
    safe_url = build_safe_clone_url(owner, repo_name)
    job = job_manager.create_job(github_url=safe_url)
    background_tasks.add_task(job_manager.run_job, job)

    return {
        "job_id": job.id,
        "status": job.status,
        "message": "Analysis queued.",
        "poll_url": f"/api/jobs/{job.id}",
        "stream_url": f"/api/jobs/{job.id}/stream",
    }


# --------------------------------------------------------------------------
# Job status
# --------------------------------------------------------------------------


@app.get(
    "/api/jobs/{job_id}",
    dependencies=[Depends(check_token), Depends(rate_limiter)],
    summary="Get analysis job status and result",
)
async def get_job_status(job_id: str, request: Request) -> dict[str, Any]:
    """Return the current status, progress, and result of an analysis job."""
    import os as _os, hmac as _hmac
    from archguard.dashboard._cookie_auth import validate_session_cookie, COOKIE_NAME

    stored_token = _os.environ.get("ARCHGUARD_DASHBOARD_TOKEN", "")
    if stored_token:
        # Try session cookie first (browser EventSource sends cookies automatically)
        cookie = request.cookies.get(COOKIE_NAME, "")
        cookie_ok = bool(cookie and validate_session_cookie(cookie, stored_token))

        # Fallback: ?token=<bearer> for non-browser SSE clients
        qs_token = request.query_params.get("token", "")
        # Use str compare_digest!
        qs_ok = False
        if qs_token:
            qs_ok = _hmac.compare_digest(qs_token.encode(), stored_token.encode())

        if not cookie_ok and not qs_ok:
            from fastapi import status as _status
            raise HTTPException(
                status_code=_status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing token",
            )

    from archguard.dashboard.job_manager import job_manager, JobStatus

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
    "/api/jobs",
    dependencies=[Depends(check_token), Depends(rate_limiter)],
    summary="List recent analysis jobs",
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
    "/api/jobs/{job_id}/stream",
    response_class=StreamingResponse,
    dependencies=[Depends(rate_limiter)],
    summary="Stream analysis progress as Server-Sent Events",
)
async def stream_job_progress(job_id: str, request: Request) -> StreamingResponse:
    """Stream real-time analysis progress for a job.
    
    Events emitted:
      {"type": "progress", "message": "..."}   — status messages from the pipeline
      {"type": "status",   "status": "..."}    — current JobStatus string
      {"type": "result",   "result": {...}}     — final AnalysisJobResult (on COMPLETE)
      {"type": "error",    "error": "..."}      — error string (on FAILED)
      {"type": "done"}                          — stream end sentinel
      
    The stream ends automatically when the job reaches COMPLETE or FAILED status.
    If the client disconnects, the stream stops but the background job continues.
    """
    import os as _os, hmac as _hmac
    from archguard.dashboard._cookie_auth import validate_session_cookie, COOKIE_NAME

    stored_token = _os.environ.get("ARCHGUARD_DASHBOARD_TOKEN", "")
    if stored_token:
        cookie = request.cookies.get(COOKIE_NAME, "")
        cookie_ok = bool(cookie and validate_session_cookie(cookie, stored_token))
        qs_token = request.query_params.get("token", "")
        qs_ok = False
        if qs_token:
            qs_ok = _hmac.compare_digest(qs_token.encode(), stored_token.encode())
        if not cookie_ok and not qs_ok:
            from fastapi import status as _status
            raise HTTPException(
                status_code=_status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing token",
            )

    from archguard.dashboard.job_manager import job_manager, JobStatus

    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    from typing import AsyncGenerator
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
    import time
    import os
    from archguard.dashboard.app import _APP_START_TIME

    return {
        "status": "ok",
        "version": importlib.metadata.version("archguard"),
        "environment": os.environ.get("ENVIRONMENT", "development"),
        "uptime_seconds": round(time.time() - _APP_START_TIME),
    }
