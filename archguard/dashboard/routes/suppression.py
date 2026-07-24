from typing import Any
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from archguard.dashboard.app import app, get_target_path, JobIdQuery
from archguard.dashboard._auth import check_token
from archguard.dashboard._rate_limit import rate_limiter
from archguard.suppression.store import SuppressionStore

@app.get("/api/v1/suppressions", dependencies=[Depends(check_token), Depends(rate_limiter)])
def get_suppressions(job_id: JobIdQuery = None) -> Any:
    target = get_target_path(job_id)
    store = SuppressionStore(target)
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
    target = get_target_path(job_id)
    store = SuppressionStore(target)
    
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
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success"}

class RemoveSuppressionRequest(BaseModel):
    suppression_id: str

@app.delete("/api/v1/suppressions", dependencies=[Depends(check_token), Depends(rate_limiter)])
def remove_suppression(req: RemoveSuppressionRequest, job_id: JobIdQuery = None) -> Any:
    target = get_target_path(job_id)
    store = SuppressionStore(target)
    if not store.delete(req.suppression_id):
        raise HTTPException(status_code=404, detail="Suppression not found")
    return {"status": "success"}
