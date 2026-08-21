"""GitHub OAuth web flow.

About eighty lines against ``httpx``, which is already a core dependency and
already the client the app uses everywhere else. An OAuth library would add a
dependency to save nothing: the authorization-code flow is one redirect and two
POSTs, and the parts that actually matter -- CSRF state, no token in a URL, a
session bound to a user id rather than to a shared secret -- are ours to get
right either way.

Only ``read:user`` is requested. ArchGuard clones public repositories over
anonymous HTTPS and never acts on the user's behalf, so anything wider would be
asking for access the product has no use for. Private-repository support
(P3-3) is a GitHub App installation, not a bigger OAuth scope.
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_API_URL = "https://api.github.com/user"

SCOPE = "read:user"
STATE_COOKIE = "archguard_oauth_state"
#: The state cookie only has to survive a round trip to github.com and back.
STATE_TTL_SECONDS = 600

_TIMEOUT = httpx.Timeout(10.0)


class OAuthError(RuntimeError):
    """The provider rejected the exchange, or answered with something unusable."""


@dataclass(frozen=True)
class GitHubIdentity:
    github_id: int
    login: str
    avatar_url: str | None


def is_configured() -> bool:
    return bool(client_id() and client_secret())


def client_id() -> str:
    return os.environ.get("GITHUB_OAUTH_CLIENT_ID", "").strip()


def client_secret() -> str:
    return os.environ.get("GITHUB_OAUTH_CLIENT_SECRET", "").strip()


def new_state() -> str:
    """A CSRF token for one authorization round trip."""
    return secrets.token_urlsafe(32)


def authorize_url(state: str, redirect_uri: str | None = None) -> str:
    params = {
        "client_id": client_id(),
        "scope": SCOPE,
        "state": state,
        # Omitted when unset so GitHub uses the callback registered on the OAuth
        # app. Sending a mismatched one is rejected, which is the correct
        # failure -- an attacker-chosen redirect_uri is how codes get stolen.
        **({"redirect_uri": redirect_uri} if redirect_uri else {}),
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(code: str, redirect_uri: str | None = None) -> str:
    """Trade an authorization code for an access token.

    The token is used once, here, to read the account and is then dropped. It is
    never stored and never reaches the browser: the session cookie is ours, and
    a leaked ArchGuard session must not also be a leaked GitHub token.
    """
    data = {
        "client_id": client_id(),
        "client_secret": client_secret(),
        "code": code,
        **({"redirect_uri": redirect_uri} if redirect_uri else {}),
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            ACCESS_TOKEN_URL, data=data, headers={"Accept": "application/json"}
        )
    if response.status_code != 200:
        # Never log the body: it echoes the code, and on some errors the
        # client_secret.
        raise OAuthError(f"token endpoint returned {response.status_code}")

    payload = response.json()
    if "error" in payload:
        # These are GitHub's own fixed error codes (bad_verification_code,
        # incorrect_client_credentials, ...), safe to record and the only way to
        # tell a stale code from a misconfigured app.
        raise OAuthError(f"token endpoint refused the exchange: {payload['error']}")

    token = payload.get("access_token")
    if not token:
        raise OAuthError("token endpoint returned no access_token")
    return str(token)


async def fetch_identity(access_token: str) -> GitHubIdentity:
    """Read the authenticated account.

    ``id`` is the identity, not ``login``: a login can be renamed and the freed
    name re-registered by someone else, so keying on it would eventually hand
    one person's history to another.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            USER_API_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
        )
    if response.status_code != 200:
        raise OAuthError(f"user endpoint returned {response.status_code}")

    payload = response.json()
    github_id = payload.get("id")
    login = payload.get("login")
    if not isinstance(github_id, int) or not login:
        raise OAuthError("user endpoint returned no usable id/login")

    return GitHubIdentity(
        github_id=github_id,
        login=str(login),
        avatar_url=payload.get("avatar_url") or None,
    )
