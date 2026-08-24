"""Watching a repository, so the worker re-scans it on a schedule.

A watch is per-user, like every other row the dashboard reads. The scheduled
pass that acts on these lives in ``archguard.worker.watch``; nothing here
touches git or the queue.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from archguard.dashboard._auth import check_token
from archguard.dashboard._identity import current_user
from archguard.dashboard._rate_limit import rate_limiter
from archguard.dashboard.routes.jobs import build_safe_clone_url, parse_github_url
from archguard.db.models import User

logger = logging.getLogger(__name__)

#: Mounted at /api/v1 by app.py. Dependencies on the router rather than on each
#: decorator, for the reason given in routes/suppression.py: repeating them per
#: route is how one ends up missing, and every route here reads user data.
router = APIRouter(dependencies=[Depends(check_token), Depends(rate_limiter)])


class WatchRequest(BaseModel):
    repo_url: str


def _canonical(repo_url: str) -> str:
    """The same URL job submission stores, or a 422.

    Built from validated owner/name parts rather than from the caller's string.
    It has to match ``build_safe_clone_url`` exactly: repositories are keyed by
    URL, so a watch stored under any other spelling would point at a different
    row than the runs it is supposed to be watching, and the trend would always
    look empty.
    """
    try:
        owner, name = parse_github_url(repo_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return build_safe_clone_url(owner, name)


@router.get("/watched")
async def list_watched_repositories(user: User = Depends(current_user)) -> Any:
    from archguard.db.session import session_scope
    from archguard.db.store import list_watched

    async with session_scope() as session:
        return {"watched": await list_watched(session, user.id)}


@router.post("/watched")
async def watch(req: WatchRequest, user: User = Depends(current_user)) -> Any:
    """Start watching. Idempotent: the caller is a toggle, not a create."""
    from archguard.db.session import session_scope
    from archguard.db.store import watch_repository

    url = _canonical(req.repo_url)
    async with session_scope() as session:
        await watch_repository(session, user.id, url)
    logger.info("User %s is now watching %s", user.id, url)
    return {"status": "watching", "repo_url": url}


@router.delete("/watched")
async def unwatch(req: WatchRequest, user: User = Depends(current_user)) -> Any:
    """Stop watching.

    Removing a watch that is not there is success, not a 404: the caller is a
    toggle, and the state it asked for is the state that now holds.
    """
    from archguard.db.session import session_scope
    from archguard.db.store import unwatch_repository

    url = _canonical(req.repo_url)
    async with session_scope() as session:
        removed = await unwatch_repository(session, user.id, url)
    return {"status": "not watching", "repo_url": url, "removed": removed}
