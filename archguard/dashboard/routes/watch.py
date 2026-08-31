"""Watched repositories: the API behind "tell me when this gets worse".

Every route here takes `user: User = Depends(current_user)` and passes
`user.id` into the store, which filters on it. There is no route that reads a
watch by id alone. Watching is the one place in the product where a URL
somebody chose gets called by our infrastructure on a schedule, so the two
things that matter most are that a watch belongs to exactly one account and
that the webhook it points at is checked before we ever call it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from archguard.dashboard._auth import check_token
from archguard.dashboard._identity import current_user
from archguard.dashboard._rate_limit import rate_limiter
from archguard.db.models import User

router = APIRouter(dependencies=[Depends(check_token), Depends(rate_limiter)])


class WatchRequest(BaseModel):
    repo_url: str = Field(..., max_length=512)
    webhook_url: str | None = Field(default=None, max_length=2048)
    #: Health points. 5 is roughly "a grade boundary" and quiet enough that a
    #: repository under normal churn does not alert every day.
    health_drop_threshold: float = Field(default=5.0, ge=0.5, le=100.0)


class WatchUpdate(BaseModel):
    active: bool | None = None
    webhook_url: str | None = Field(default=None, max_length=2048)
    health_drop_threshold: float | None = Field(default=None, ge=0.5, le=100.0)


async def _check_webhook(url: str | None) -> None:
    """Refuse a webhook URL the SSRF guard rejects.

    Checked at configuration time so the user is told immediately rather than
    discovering days later that their alerts go nowhere. It is checked *again*
    at send time, in `send_generic_webhook`, because a hostname that resolved
    to a public address today can be repointed at an internal one tomorrow --
    this check is a courtesy, that one is the control.

    Only the send-time call keeps its answer: it connects to the address the
    check approved, so nothing can be repointed between the two. The result is
    discarded here because nothing is being delivered yet.
    """
    if not url:
        return

    import asyncio

    from archguard.utils.url_validator import validate_webhook_url

    try:
        # Blocking getaddrinfo; off the event loop.
        await asyncio.to_thread(validate_webhook_url, url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Webhook URL rejected: {exc}") from exc


@router.get("/watch")
async def list_watches(user: User = Depends(current_user)) -> dict[str, object]:
    from archguard.db import store
    from archguard.db.session import session_scope

    async with session_scope() as session:
        return {"watched": await store.list_watched(session, user.id)}


@router.post("/watch", status_code=201)
async def create_watch(
    body: WatchRequest, user: User = Depends(current_user)
) -> dict[str, object]:
    from archguard.dashboard.routes.jobs import parse_github_url
    from archguard.db import store
    from archguard.db.session import session_scope

    # The same parser the submit form uses, so a URL that cannot be analysed
    # cannot be watched either.
    try:
        parse_github_url(body.repo_url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await _check_webhook(body.webhook_url)

    async with session_scope() as session:
        watch = await store.watch_repository(
            session,
            user.id,
            body.repo_url,
            webhook_url=body.webhook_url,
            health_drop_threshold=body.health_drop_threshold,
        )
        await session.flush()
        watch_id = watch.id
        return {"watched": await store.get_watched_summary(session, watch_id, user.id)}


@router.patch("/watch/{watch_id}")
async def update_watch(
    watch_id: int, body: WatchUpdate, user: User = Depends(current_user)
) -> dict[str, object]:
    from archguard.db import store
    from archguard.db.session import session_scope

    async with session_scope() as session:
        # Ownership first, then the URL. Validating a stranger's webhook before
        # checking whether the watch is theirs would resolve a hostname they
        # chose on behalf of a request we are about to refuse, and would answer
        # 400 where the whole point is to answer 404.
        if await store.get_watched(session, watch_id, user.id) is None:
            raise HTTPException(status_code=404, detail="Watched repository not found")

    await _check_webhook(body.webhook_url)

    async with session_scope() as session:
        updated = await store.update_watched(
            session,
            watch_id,
            user.id,
            active=body.active,
            webhook_url=body.webhook_url,
            health_drop_threshold=body.health_drop_threshold,
        )
        if updated is None:
            # 404, not 403. Confirming that an id exists but belongs to someone
            # else is a slower way of leaking the same fact.
            raise HTTPException(status_code=404, detail="Watched repository not found")
        return {"watched": await store.get_watched_summary(session, watch_id, user.id)}


@router.delete("/watch/{watch_id}", status_code=204)
async def delete_watch(watch_id: int, user: User = Depends(current_user)) -> None:
    from archguard.db import store
    from archguard.db.session import session_scope

    async with session_scope() as session:
        if not await store.delete_watched(session, watch_id, user.id):
            raise HTTPException(status_code=404, detail="Watched repository not found")
