import logging
from typing import Any
from pathlib import Path
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from archguard.dashboard.app import app, JobIdQuery
from archguard.dashboard._auth import check_token
from archguard.dashboard._rate_limit import rate_limiter
from archguard.suppression.store import SuppressionStore


def _suppression_store(job_id: str | None) -> SuppressionStore:
    """Return a SuppressionStore rooted in a durable location (not the ephemeral clone)."""
    base = Path.cwd() / ".archguard-cache"
    base.mkdir(parents=True, exist_ok=True)
    if job_id:
        # Per-job suppression file, survives workspace deletion
        store_path = base / f"suppressions-{job_id}.jsonl"
    else:
        store_path = base / "suppressions.jsonl"
    # SuppressionStore expects repo_root; it appends SUPPRESSION_FILE internally.
    # We use a thin wrapper: pass a fake root so _path resolves to our durable file.
    # Simpler: construct with cwd and override _path.
    store = SuppressionStore(Path.cwd())
    store._path = store_path
    store._lock_path = store_path.with_suffix(".lock")
    store._cache = None
    return store


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
