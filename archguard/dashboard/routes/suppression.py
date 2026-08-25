import logging
from datetime import UTC
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from archguard.dashboard._auth import check_token
from archguard.dashboard._identity import current_user
from archguard.dashboard._rate_limit import rate_limiter
from archguard.dashboard._workspace_paths import JobIdQuery
from archguard.db.models import Suppression, User

#: Mounted at /api/v1 by app.py. The dependencies live on the router rather
#: than on each decorator: repeating them per route is how one of them ends up
#: missing, and every route below this line reads user data.
router = APIRouter(dependencies=[Depends(check_token), Depends(rate_limiter)])



async def repo_url_for_job(job_id: str | None, user_id: int) -> str | None:
    """Resolve a job id to the repository it analysed.

    The jobs table is the durable answer; the in-memory job map is only a
    fast path for a job this process is still running. Suppressions must
    outlive both a job and a server restart, so a lookup that only consulted
    process memory would silently start a fresh store after every deploy --
    which is what the orphaned ``suppressions-<uuid>.jsonl`` files are.
    """
    if not job_id:
        return None

    # The in-memory map is not consulted first any more: it records no owner,
    # so trusting it would answer "which repository is job X?" for a job that
    # belongs to someone else. The jobs table knows, and is also the only one of
    # the two that survives a restart.
    try:
        from archguard.db.session import session_scope
        from archguard.db.store import get_job_repo_url

        async with session_scope() as session:
            return await get_job_repo_url(session, job_id, user_id)
    except Exception as exc:
        logging.getLogger(__name__).debug("Job row lookup failed for %s: %s", job_id, exc)

    return None


def _as_payload(s: Suppression) -> dict[str, Any]:
    """One row as the dashboard's JavaScript expects it.

    Explicit rather than ``s.__dict__``, which is what this returned while
    suppressions were dataclasses in a file. On an ORM row that attribute
    carries SQLAlchemy's internal instance state, and ``user_id`` besides --
    another user's id is not something a response body should be handing out.
    """
    return {
        "id": s.id,
        "module": s.module,
        "layer": s.layer,
        "violation_hash": s.violation_hash,
        "reason": s.reason,
        "created_by": s.created_by,
        "commit_sha": s.commit_sha,
        "pr_number": s.pr_number,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "expires_at": s.expires_at.isoformat() if s.expires_at else None,
        "active": s.active,
    }


@router.get("/suppressions")
async def get_suppressions(
    job_id: JobIdQuery = None, user: User = Depends(current_user)
) -> Any:
    repo_url = await repo_url_for_job(job_id, user.id)
    if not repo_url:
        return {"suppressions": []}

    from archguard.db.session import session_scope
    from archguard.db.store import list_suppressions

    async with session_scope() as session:
        rows = await list_suppressions(session, repo_url, user.id, include_inactive=True)
        return {"suppressions": [_as_payload(s) for s in rows]}

class AddSuppressionRequest(BaseModel):
    module: str
    layer: int
    message: str
    reason: str
    expires_in_days: int | None = None
    pr_number: int | None = None
    commit_sha: str | None = None

@router.post("/suppressions")
async def add_suppression(
    req: AddSuppressionRequest,
    job_id: JobIdQuery = None,
    user: User = Depends(current_user),
) -> Any:
    repo_url = await repo_url_for_job(job_id, user.id)
    if not repo_url:
        # Without a repository there is nowhere durable to put this. The file
        # store used to accept it into a job-scoped file that the next scan
        # would never find again, which read as success and was not.
        raise HTTPException(
            status_code=400,
            detail="Could not resolve a repository for this job.",
        )

    expires_at = None
    if req.expires_in_days:
        from datetime import datetime, timedelta

        expires_at = datetime.now(UTC) + timedelta(days=req.expires_in_days)

    from archguard.db.session import session_scope
    from archguard.db.store import add_suppression as store_add
    from archguard.suppression.models import make_violation_hash

    try:
        async with session_scope() as session:
            await store_add(
                session,
                repo_url=repo_url,
                module=req.module,
                layer=req.layer,
                violation_hash=make_violation_hash(req.module, req.layer, req.message),
                reason=req.reason,
                expires_at=expires_at,
                pr_number=req.pr_number,
                commit_sha=req.commit_sha or "",
                user_id=user.id,
            )
    except Exception as e:
        logging.getLogger(__name__).warning("Suppression add failed: %s", e)
        raise HTTPException(status_code=400, detail="Failed to add suppression.")
    return {"status": "success"}

class RemoveSuppressionRequest(BaseModel):
    suppression_id: str

@router.delete("/suppressions")
async def remove_suppression(
    req: RemoveSuppressionRequest,
    job_id: JobIdQuery = None,
    user: User = Depends(current_user),
) -> Any:
    from archguard.db.session import session_scope
    from archguard.db.store import delete_suppression

    async with session_scope() as session:
        # Scoped by user inside the query. Somebody else's suppression is
        # reported as absent rather than as forbidden, which is also the honest
        # answer: as far as this user is concerned, it does not exist.
        if not await delete_suppression(session, req.suppression_id, user.id):
            raise HTTPException(status_code=404, detail="Suppression not found")
    return {"status": "success"}
