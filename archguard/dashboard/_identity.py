"""Resolving the caller to a user.

``check_token`` answered one question -- may this request through? -- and every
route below it then read whatever data it liked, because there was nothing in
the request that said *whose* data it was. That is D1: a single shared token,
and any holder could enumerate every job id and read every other visitor's
repository URLs, module names, file paths and violations.

``current_user`` answers the useful question instead. Everything downstream
filters on the id it returns, so isolation is a property of the query rather
than a rule each route has to remember.
"""

from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request, status

from archguard.dashboard import _sessions
from archguard.db.models import User

logger = logging.getLogger(__name__)

#: The account local development runs as when no OAuth app is configured. It is
#: a real row, so every query is exercised exactly as it will be in production;
#: only the way it is reached is different.
DEV_LOGIN = "local-dev"
DEV_GITHUB_ID = 0


def _is_production() -> bool:
    return os.environ.get("ENVIRONMENT", "").lower() == "production"


def dev_login_permitted(request: Request) -> bool:
    """Whether this request may fall back to the local development account.

    Three conditions, all required, none of them a toggle a deployment can set
    by accident:

    * not production -- ``ENVIRONMENT`` says so;
    * no OAuth app configured -- once there is a way to sign in properly, this
      path stops existing rather than sitting there as a second one;
    * the peer is loopback -- the *direct* peer, never the X-Forwarded-For
      derived address, because a forwarded request did not originate locally by
      definition and honouring the header here would let any client claim it
      did.

    The production config check (P0-6) refuses to start without OAuth
    configured, so production cannot reach this function's ``True`` branch even
    if ``ENVIRONMENT`` were wrong.
    """
    if _is_production():
        return False

    from archguard.dashboard import _oauth

    if _oauth.is_configured():
        return False

    from archguard.dashboard._auth import _ALWAYS_TRUSTED_HOSTS, _direct_client_ip

    host = _direct_client_ip(request)
    if host in _ALWAYS_TRUSTED_HOSTS:
        return True
    import ipaddress

    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def _dev_user() -> User:
    """Get or create the local development account."""
    from sqlalchemy import select

    from archguard.db.session import session_scope

    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.github_id == DEV_GITHUB_ID))
        ).scalar_one_or_none()
        if user is None:
            user = User(github_id=DEV_GITHUB_ID, login=DEV_LOGIN, avatar_url=None)
            session.add(user)
            await session.flush()
        return user


async def _user_by_id(user_id: int) -> User | None:
    from archguard.db.session import session_scope

    async with session_scope() as session:
        return await session.get(User, user_id)


async def current_user(request: Request) -> User:
    """The signed-in user, or 401.

    Resolution order, most specific first:

    1. the session cookie, which is the only path a browser ever uses;
    2. the local development account, under the conditions above.

    ``ARCHGUARD_DASHBOARD_TOKEN`` is deliberately **not** accepted here, and no
    ``Authorization`` header is read at all -- declaring one would advertise a
    bearer scheme in the OpenAPI schema that this function then ignores. It is
    an operator credential for ``/health`` and future admin endpoints, and it
    identifies no one; honouring it on a data route would mean answering
    "whose rows?" with "any of them", which is the exact hole this replaces.
    """
    user_id = _sessions.resolve(request.cookies.get(_sessions.COOKIE_NAME, ""))
    if user_id is not None:
        user = await _user_by_id(user_id)
        if user is not None:
            return user
        # A valid signature over a session whose user no longer exists: the
        # account was deleted while the cookie was still live.
        logger.info("Session resolved to a user id that no longer exists")

    if dev_login_permitted(request):
        return await _dev_user()

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sign in with GitHub to continue.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def optional_user(request: Request) -> User | None:
    """The signed-in user, or None -- for routes that serve both."""
    try:
        return await current_user(request)
    except HTTPException:
        return None
