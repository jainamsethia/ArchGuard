import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from archguard.dashboard._auth import check_token
from archguard.dashboard._identity import current_user
from archguard.dashboard._rate_limit import rate_limiter
from archguard.dashboard._workspace_paths import JobIdQuery
from archguard.db.models import User

#: Mounted at /api/v1 by app.py. The dependencies live on the router rather
#: than on each decorator: repeating them per route is how one of them ends up
#: missing, and every route below this line reads user data.
router = APIRouter(dependencies=[Depends(check_token), Depends(rate_limiter)])



async def repo_url_for_job(job_id: str | None, user_id: int) -> str | None:
    """Resolve a job id to the repository it analysed, for this user.

    Scoped, and that is the point: the job id is the only thing a caller
    supplies that names a repository, so it is the obvious thing to borrow.
    ``get_job_repo_url`` filters on the owner, so a job belonging to somebody
    else resolves to None and the route answers 404 -- the same answer as an id
    that does not exist, which is the fact worth withholding.

    A suppression outlives the job that revealed the finding: every scan mints
    a new job id, and a suppression is only ever useful on the *next* scan. So
    this resolves to the repository, and the repository plus the owner is what
    the suppression is stored against.
    """
    if not job_id:
        return None

    # The jobs table rather than the in-memory job map: the map records no
    # owner, so trusting it would answer "which repository is job X?" for a job
    # that belongs to someone else -- and it does not survive a restart.
    try:
        from archguard.db.session import session_scope
        from archguard.db.store import get_job_repo_url

        async with session_scope() as session:
            return await get_job_repo_url(session, job_id, user_id)
    except Exception as exc:
        logging.getLogger(__name__).debug("Job row lookup failed for %s: %s", job_id, exc)

    return None


@router.get("/suppressions")
async def get_suppressions(
    job_id: JobIdQuery = None, user: User = Depends(current_user)
) -> Any:
    from archguard.db import store
    from archguard.db.session import session_scope

    repo_url = await repo_url_for_job(job_id, user.id)
    if not repo_url:
        return {"suppressions": []}

    async with session_scope() as session:
        rows = await store.list_suppressions(session, user.id, repo_url)
        return {"suppressions": [_to_dict(r) for r in rows]}


class AddSuppressionRequest(BaseModel):
    module: str = Field(..., max_length=200)
    layer: int = Field(..., ge=1, le=4)
    message: str = Field(..., max_length=2000)
    reason: str = Field(..., max_length=500)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    pr_number: int | None = None
    commit_sha: str | None = Field(default=None, max_length=40)


@router.post("/suppressions")
async def add_suppression(
    req: AddSuppressionRequest,
    job_id: JobIdQuery = None,
    user: User = Depends(current_user),
) -> Any:
    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.suppression.models import make_violation_hash

    repo_url = await repo_url_for_job(job_id, user.id)
    if not repo_url:
        # The job is not this user's, or does not exist. Same answer either
        # way: confirming which would tell a stranger that an id is real.
        raise HTTPException(status_code=404, detail="Job not found")

    expires_at = None
    if req.expires_in_days:
        expires_at = datetime.now(UTC) + timedelta(days=req.expires_in_days)

    try:
        async with session_scope() as session:
            await store.add_suppression(
                session,
                user_id=user.id,
                repo_url=repo_url,
                module=req.module,
                layer=req.layer,
                # The same hash the analysis matches on, from the same
                # function, so a suppression cannot be recorded under an
                # identity the filter will never look up.
                violation_hash=make_violation_hash(req.module, req.layer, req.message),
                reason=req.reason,
                expires_at=expires_at,
                pr_number=req.pr_number,
                commit_sha=req.commit_sha or "",
                created_by=user.login or "unknown",
            )
    except Exception as exc:
        logging.getLogger(__name__).warning("Suppression add failed: %s", exc)
        raise HTTPException(status_code=400, detail="Failed to add suppression.") from exc
    return {"status": "success"}


class RemoveSuppressionRequest(BaseModel):
    suppression_id: str = Field(..., max_length=64)


@router.delete("/suppressions")
async def remove_suppression(
    req: RemoveSuppressionRequest,
    job_id: JobIdQuery = None,
    user: User = Depends(current_user),
) -> Any:
    from archguard.db import store
    from archguard.db.session import session_scope

    async with session_scope() as session:
        # Ownership is settled inside the store, which returns False for both
        # "no such id" and "not yours" so this route cannot accidentally tell
        # the two apart.
        if not await store.delete_suppression(session, req.suppression_id, user.id):
            raise HTTPException(status_code=404, detail="Suppression not found")
    return {"status": "success"}


def _to_dict(row: Any) -> dict[str, Any]:
    """The shape the dashboard already renders.

    Explicit rather than ``__dict__``: that carried SQLAlchemy's internal
    state, and it would hand out ``user_id`` -- which is nobody's business but
    the owner's, and useless to them.
    """
    return {
        "id": row.id,
        "module": row.module,
        "layer": row.layer,
        "violation_hash": row.violation_hash,
        "reason": row.reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "created_by": row.created_by,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "pr_number": row.pr_number,
        "commit_sha": row.commit_sha,
        "active": row.active,
    }
