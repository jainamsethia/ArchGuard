"""Sign-in, sign-out, and who-am-I.

Replaces the token-exchange login, where every visitor who pasted the one shared
operator token got an identical session and therefore identical access to
everything anyone had ever analysed.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from archguard.dashboard import _oauth, _sessions
from archguard.dashboard._identity import dev_login_permitted, optional_user
from archguard.dashboard._rate_limit import rate_limiter

logger = logging.getLogger(__name__)

#: Mounted at /api/v1. No check_token: /auth/status is what the page asks
#: before it knows whether it is signed in, so it has to be answerable while
#: signed out.
router = APIRouter(dependencies=[Depends(rate_limiter)])

#: Unprefixed: the OAuth callback URL is registered with GitHub, and burying it
#: under /api/v1 would make it look like an API endpoint rather than a
#: browser redirect target.
oauth_router = APIRouter(dependencies=[Depends(rate_limiter)])


def _secure_cookies() -> bool:
    return os.environ.get("ENVIRONMENT", "").lower() == "production"


def _callback_url(request: Request) -> str | None:
    """The redirect_uri to send, or None to use the one registered on the app.

    Set ``GITHUB_OAUTH_REDIRECT_URI`` when the public origin is not what the
    app sees -- behind a proxy that terminates TLS, ``request.url_for`` builds
    an ``http://`` URL for an ``https://`` site, and GitHub rejects the
    mismatch.
    """
    configured = os.environ.get("GITHUB_OAUTH_REDIRECT_URI", "").strip()
    return configured or None


@oauth_router.get("/auth/github", include_in_schema=False)
async def github_login(request: Request) -> Response:
    """Start the OAuth flow."""
    if not _oauth.is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Sign-in is not configured on this instance. Set "
                "GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET."
            ),
        )

    state = _oauth.new_state()
    response = RedirectResponse(
        _oauth.authorize_url(state, _callback_url(request)), status_code=302
    )
    response.set_cookie(
        key=_oauth.STATE_COOKIE,
        value=state,
        httponly=True,
        # Lax, not Strict: the browser arrives back here from github.com, and a
        # Strict cookie is not sent on a cross-site navigation -- so the state
        # check would fail every single time.
        samesite="lax",
        secure=_secure_cookies(),
        max_age=_oauth.STATE_TTL_SECONDS,
        path="/",
    )
    return response


@oauth_router.get("/auth/github/callback", include_in_schema=False)
async def github_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Finish the OAuth flow and issue a session."""
    if error:
        # The user pressed Cancel, most often. Not an error page.
        logger.info("GitHub OAuth returned error=%s", error)
        return RedirectResponse("/?auth=cancelled", status_code=302)

    expected = request.cookies.get(_oauth.STATE_COOKIE, "")
    if not expected or not state or not _constant_time_eq(state, expected):
        # Without this, an attacker can complete the flow with their own code
        # in the victim's browser and silently bind the victim's session to the
        # attacker's account.
        raise HTTPException(status_code=400, detail="Invalid or expired sign-in state.")

    if not code:
        raise HTTPException(status_code=400, detail="No authorization code returned.")

    try:
        access_token = await _oauth.exchange_code(code, _callback_url(request))
        identity = await _oauth.fetch_identity(access_token)
    except _oauth.OAuthError as exc:
        logger.warning("GitHub OAuth exchange failed: %s", exc)
        raise HTTPException(status_code=502, detail="GitHub sign-in failed. Try again.")

    from archguard.db.session import session_scope
    from archguard.db.store import upsert_user

    async with session_scope() as session:
        user = await upsert_user(
            session,
            github_id=identity.github_id,
            login=identity.login,
            avatar_url=identity.avatar_url,
        )
        user_id = user.id

    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        key=_sessions.COOKIE_NAME,
        value=_sessions.issue(user_id),
        httponly=True,
        samesite="lax",
        secure=_secure_cookies(),
        max_age=_sessions.ttl_seconds(),
        path="/",
    )
    response.delete_cookie(key=_oauth.STATE_COOKIE, path="/")
    logger.info("Signed in user %s (github_id=%s)", identity.login, identity.github_id)
    return response


def _constant_time_eq(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a.encode(), b.encode())


@router.post("/auth/logout", include_in_schema=False)
async def logout(request: Request, response: Response) -> dict[str, bool]:
    """Invalidate the current session."""
    cookie_value = request.cookies.get(_sessions.COOKIE_NAME, "")
    if cookie_value:
        _sessions.revoke(cookie_value)
    response.delete_cookie(key=_sessions.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/auth/status", include_in_schema=False)
async def auth_status(request: Request, user: Any = Depends(optional_user)) -> Any:
    """Who the caller is, if anyone.

    Bypasses ``current_user``'s 401 on purpose: this is the endpoint the page
    asks before it knows whether to show a sign-in button, so it has to be
    answerable while signed out.
    """
    if user is None:
        return {
            "authenticated": False,
            "sign_in_url": "/auth/github" if _oauth.is_configured() else None,
            "sign_in_available": _oauth.is_configured() or dev_login_permitted(request),
        }
    return {
        "authenticated": True,
        "sign_in_available": True,
        "user": {
            "login": user.login,
            "avatar_url": user.avatar_url,
        },
    }
