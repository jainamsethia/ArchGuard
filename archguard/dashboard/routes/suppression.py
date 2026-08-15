import logging
from typing import Any
from pathlib import Path
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from archguard.dashboard.app import app, JobIdQuery
from archguard.dashboard._auth import check_token
from archguard.dashboard._rate_limit import rate_limiter
from archguard.suppression.store import SuppressionStore


def repo_url_for_job(job_id: str | None) -> str | None:
    """Resolve a job id to the repository it analysed.

    Checked in order of durability: the in-memory job manager knows about jobs
    from this process, and the audit log still knows about them after a restart.
    Suppressions must outlive both a job and a server restart, so the audit log
    fallback matters -- without it a restart would silently start a new store.
    """
    if not job_id:
        return None

    try:
        from archguard.dashboard.job_manager import job_manager

        job = job_manager.get_job(job_id)
        if job is not None and job.github_url:
            return str(job.github_url)
    except Exception as exc:  # noqa: BLE001 - fall through to the durable source
        logging.getLogger(__name__).debug("Job lookup failed for %s: %s", job_id, exc)

    try:
        from archguard.dashboard.app import get_audit_path
        from archguard.audit.logger import AuditLogger

        for run in reversed(AuditLogger(get_audit_path(job_id)).read_last_n_runs(n=200)):
            if run.get("job_id") == job_id and run.get("repo_url"):
                return str(run["repo_url"])
    except Exception as exc:  # noqa: BLE001 - absence is handled by the caller
        logging.getLogger(__name__).debug("Audit lookup failed for %s: %s", job_id, exc)

    return None


def suppression_base_dir() -> Path:
    """Durable root for dashboard suppressions (never the ephemeral clone)."""
    base = Path.cwd() / ".archguard-cache"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _suppression_store(job_id: str | None) -> SuppressionStore:
    """Return the durable, repository-keyed SuppressionStore for a job.

    Keyed by ``owner/repo`` rather than ``job_id``: every scan creates a new job
    id, so a job-scoped store could never be found again on the next scan of the
    same repository -- which is the only time a suppression is any use.
    """
    from archguard.suppression.scope import suppression_path_for_repo

    base = suppression_base_dir()
    repo_url = repo_url_for_job(job_id)

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
def get_suppressions(job_id: JobIdQuery = None) -> Any:
    store = _suppression_store(job_id)
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
def add_suppression(req: AddSuppressionRequest, job_id: JobIdQuery = None) -> Any:
    store = _suppression_store(job_id)

    expires_at = None
    if req.expires_in_days:
        from datetime import datetime, timedelta, timezone
        expires_at = (datetime.now(timezone.utc) + timedelta(days=req.expires_in_days)).isoformat()

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
def remove_suppression(req: RemoveSuppressionRequest, job_id: JobIdQuery = None) -> Any:
    store = _suppression_store(job_id)
    if not store.delete(req.suppression_id):
        raise HTTPException(status_code=404, detail="Suppression not found")
    return {"status": "success"}
