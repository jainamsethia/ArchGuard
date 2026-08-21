import logging
from datetime import UTC
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from archguard.dashboard._auth import check_token
from archguard.dashboard._rate_limit import rate_limiter
from archguard.dashboard.app import JobIdQuery, app
from archguard.suppression.store import SuppressionStore


async def repo_url_for_job(job_id: str | None) -> str | None:
    """Resolve a job id to the repository it analysed.

    The jobs table is the durable answer; the in-memory job map is only a
    fast path for a job this process is still running. Suppressions must
    outlive both a job and a server restart, so a lookup that only consulted
    process memory would silently start a fresh store after every deploy --
    which is what the orphaned ``suppressions-<uuid>.jsonl`` files are.
    """
    if not job_id:
        return None

    try:
        from archguard.dashboard.job_manager import job_manager

        job = job_manager.get_job(job_id)
        if job is not None and job.github_url:
            return str(job.github_url)
    except Exception as exc:
        logging.getLogger(__name__).debug("Job lookup failed for %s: %s", job_id, exc)

    try:
        from archguard.db.session import session_scope
        from archguard.db.store import get_job_repo_url

        async with session_scope() as session:
            return await get_job_repo_url(session, job_id)
    except Exception as exc:
        logging.getLogger(__name__).debug("Job row lookup failed for %s: %s", job_id, exc)

    return None


def suppression_base_dir() -> Path:
    """Durable root for dashboard suppressions (never the ephemeral clone)."""
    base = Path.cwd() / ".archguard-cache"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _suppression_store(
    repo_url: str | None, job_id: str | None = None
) -> SuppressionStore:
    """Return the durable, repository-keyed SuppressionStore.

    Keyed by ``owner/repo`` rather than ``job_id``: every scan creates a new job
    id, so a job-scoped store could never be found again on the next scan of the
    same repository -- which is the only time a suppression is any use.

    The repository is passed in rather than looked up, because both callers
    already know it: a route has resolved the job, and ``_selection`` has the
    run, which records its own ``repo_url``.
    """
    from archguard.suppression.scope import suppression_path_for_repo

    base = suppression_base_dir()

    if repo_url:
        store_path = suppression_path_for_repo(base, repo_url)
    elif job_id:
        # Repository unknown (job evicted and no audit entry). Fall back to the
        # old per-job file so the request still works; it just won't be found by
        # the next scan. Logged because that is a real limitation, not a detail.
        logging.getLogger(__name__).warning(
            "Could not resolve a repository for job %s; using a job-scoped "
            "suppression store that will not persist across scans.", job_id,
        )
        store_path = base / f"suppressions-{job_id}.jsonl"
    else:
        store_path = base / "suppressions.jsonl"

    store_path.parent.mkdir(parents=True, exist_ok=True)
    return SuppressionStore.at_path(store_path)


@app.get("/api/v1/suppressions", dependencies=[Depends(check_token), Depends(rate_limiter)])
async def get_suppressions(job_id: JobIdQuery = None) -> Any:
    store = _suppression_store(await repo_url_for_job(job_id), job_id)
    suppressions = store.list_all(include_inactive=True)
    return {"suppressions": [s.__dict__ for s in suppressions]}

class AddSuppressionRequest(BaseModel):
    module: str
    layer: int
    message: str
    reason: str
    expires_in_days: int | None = None
    pr_number: int | None = None
    commit_sha: str | None = None

@app.post("/api/v1/suppressions", dependencies=[Depends(check_token), Depends(rate_limiter)])
async def add_suppression(req: AddSuppressionRequest, job_id: JobIdQuery = None) -> Any:
    store = _suppression_store(await repo_url_for_job(job_id), job_id)

    expires_at = None
    if req.expires_in_days:
        from datetime import datetime, timedelta
        expires_at = (datetime.now(UTC) + timedelta(days=req.expires_in_days)).isoformat()

    try:
        store.add(
            module=req.module,
            layer=req.layer,
            message=req.message,
            reason=req.reason,
            expires_at=expires_at,
            pr_number=req.pr_number,
            commit_sha=req.commit_sha or ""
        )
    except Exception as e:
        logging.getLogger(__name__).warning("Suppression add failed: %s", e)
        raise HTTPException(status_code=400, detail="Failed to add suppression.")
    return {"status": "success"}

class RemoveSuppressionRequest(BaseModel):
    suppression_id: str

@app.delete("/api/v1/suppressions", dependencies=[Depends(check_token), Depends(rate_limiter)])
async def remove_suppression(req: RemoveSuppressionRequest, job_id: JobIdQuery = None) -> Any:
    store = _suppression_store(await repo_url_for_job(job_id), job_id)
    if not store.delete(req.suppression_id):
        raise HTTPException(status_code=404, detail="Suppression not found")
    return {"status": "success"}
