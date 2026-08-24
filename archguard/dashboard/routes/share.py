"""Share a single analysis by link.

Two surfaces with opposite audiences. ``/api/v1/runs/{job_id}/share`` is
authenticated and owner-scoped; it mints and revokes the token. ``/shared/{token}``
is deliberately unauthenticated, because a link nobody can open without an
account is not a share -- and the token is therefore the entire credential for
it.

The shared page is server-rendered from the same partial the example report
uses. It never loads the dashboard bundle, so a shared link cannot become a
starting point for calling the authenticated API from someone else's browser.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from archguard.dashboard._auth import check_token
from archguard.dashboard._identity import current_user
from archguard.dashboard._rate_limit import rate_limiter
from archguard.db.models import User

logger = logging.getLogger(__name__)

#: Mounted at /api/v1 by app.py.
router = APIRouter(dependencies=[Depends(check_token), Depends(rate_limiter)])

#: Mounted at the root, without the auth dependency. Rate limited, because it
#: is the one data route an anonymous caller can reach and a token guess costs
#: a request.
public_router = APIRouter(dependencies=[Depends(rate_limiter)])


def _share_url(request: Request, token: str) -> str:
    return str(request.url_for("serve_shared_report", token=token))


@router.post("/runs/{job_id}/share")
async def create_share_link(
    job_id: str, request: Request, user: User = Depends(current_user)
) -> Any:
    """Mint (or return) the link for one of your own runs."""
    from archguard.db.session import session_scope
    from archguard.db.store import share_run

    async with session_scope() as session:
        token = await share_run(session, job_id, user.id)

    if token is None:
        # Same answer for "no such run" and "not yours", so this cannot be used
        # to find out which job ids exist.
        raise HTTPException(status_code=404, detail="No such run.")

    # The run, never the token: a share link in the logs is a share link
    # readable by anyone who can read the logs.
    logger.info("Run for job %s shared by user %s", job_id, user.id)
    return {"status": "shared", "share_url": _share_url(request, token)}


@router.delete("/runs/{job_id}/share")
async def revoke_share_link(job_id: str, user: User = Depends(current_user)) -> Any:
    """Revoke the link. Success even if there was nothing to revoke."""
    from archguard.db.session import session_scope
    from archguard.db.store import unshare_run

    async with session_scope() as session:
        revoked = await unshare_run(session, job_id, user.id)

    if revoked:
        logger.info("Share link for job %s revoked by user %s", job_id, user.id)
    return {"status": "not shared", "revoked": revoked}


@public_router.get("/shared/{token}", include_in_schema=False, name="serve_shared_report")
async def serve_shared_report(token: str, request: Request) -> Response:
    """The shared report, to anyone holding the link.

    404 for an unknown, revoked or malformed token -- never 401 or 403. A
    different status for "this token was valid once" would confirm that a
    guessed token had at some point existed.
    """
    from archguard.dashboard.app import _templates
    from archguard.db.session import session_scope
    from archguard.db.store import get_shared_run

    async with session_scope() as session:
        run = await get_shared_run(session, token)

    if run is None:
        raise HTTPException(status_code=404, detail="This link is not valid.")

    repo_url = run.get("repo_url") or ""
    display = repo_url.removeprefix("https://github.com/").removesuffix(".git")

    response = _templates.TemplateResponse(
        request,
        "shared.html",
        {
            "csp_nonce": getattr(request.state, "csp_nonce", ""),
            "run": run,
            "display_repo": display or "a repository",
        },
    )
    # A revoked link must stop working immediately, which it cannot do if a
    # proxy or the browser is still serving the last copy.
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response
