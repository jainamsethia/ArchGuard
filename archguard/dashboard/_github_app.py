"""GitHub App installation auth, for reading private repositories (P3-3).

``_oauth`` answers "who is this person?". This module answers a different
question -- "may this deployment read that repository?" -- and the two are not
interchangeable. A user's OAuth token is a credential for acting *as them*, so
cloning with it would mean holding, refreshing and eventually leaking a
credential that opens every repository they can see. An App installation is
scoped by the repository owner to the repositories they chose, its tokens expire
within the hour, and nothing it issues can act as the user. That is why the
OAuth scope stayed at ``read:user`` rather than growing a ``repo``.

The flow is three calls and no new library:

1. sign a short-lived JWT with the App's private key (RS256, App id as issuer);
2. ask which installation covers a repository, using that JWT;
3. exchange the JWT for an installation token scoped to that repository.

Tokens are cached until shortly before they expire, because step 3 is rate
limited while an analysis run clones once per job.

The private key is the only secret read from the environment. The tokens it
mints are held in memory, never logged and never written to disk.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime

import httpx
import jwt

logger = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"

#: GitHub rejects an App JWT whose lifetime exceeds ten minutes. Nine leaves
#: room for the backdating below without crossing that limit.
JWT_TTL_SECONDS = 540
#: GitHub's own guidance: backdate ``iat`` to tolerate a fast local clock, which
#: otherwise produces a 401 that reads like a bad key.
JWT_BACKDATE_SECONDS = 60
#: Refresh before expiry rather than at it. A token that dies between minting
#: and the clone is indistinguishable from a revoked installation.
TOKEN_REFRESH_MARGIN_SECONDS = 300
#: GitHub's documented installation-token lifetime, used only as a fallback.
TOKEN_ASSUMED_LIFETIME_SECONDS = 3600

_TIMEOUT = httpx.Timeout(10.0)

_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"


class GitHubAppError(RuntimeError):
    """The App is misconfigured, or GitHub refused the exchange."""


@dataclass(frozen=True)
class InstallationToken:
    """A short-lived token scoped to one installation.

    ``repr`` is overridden because this object travels through log lines,
    exception context and test output, and the default dataclass ``repr`` would
    print the secret in all three.
    """

    token: str = field(repr=False)
    expires_at: float

    def __repr__(self) -> str:
        return f"InstallationToken(expires_at={self.expires_at!r})"

    def is_usable(self, *, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        return self.expires_at - TOKEN_REFRESH_MARGIN_SECONDS > current


def app_id() -> str:
    return os.environ.get("GITHUB_APP_ID", "").strip()


def private_key() -> str:
    r"""The App's PEM private key.

    Accepts the three shapes a deployment realistically produces: the PEM as-is,
    the same PEM with literal ``\n`` escapes (which is how most dashboards
    mangle a multi-line secret), and base64 of the PEM (what people reach for
    once the escaping has bitten them).
    """
    raw = os.environ.get("GITHUB_APP_PRIVATE_KEY", "").strip()
    if not raw:
        return ""
    if "-----BEGIN" in raw:
        return raw.replace("\\n", "\n")
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        # Not base64 either. Hand it back unchanged so signing fails with a
        # message about the key, rather than guessing further here.
        return raw
    return decoded if "-----BEGIN" in decoded else raw


def is_configured() -> bool:
    """Whether private-repository support is available on this deployment.

    Deliberately not an error when false. A deployment without an App is the
    normal case and stays fully functional for public repositories.
    """
    return bool(app_id() and private_key())


def app_jwt(*, now: float | None = None) -> str:
    """Sign the App-level JWT that steps 1 and 2 authenticate with."""
    if not is_configured():
        raise GitHubAppError(
            "GitHub App is not configured: set GITHUB_APP_ID and "
            "GITHUB_APP_PRIVATE_KEY to read private repositories."
        )
    issued = int(time.time() if now is None else now)
    payload = {
        "iat": issued - JWT_BACKDATE_SECONDS,
        "exp": issued + JWT_TTL_SECONDS,
        "iss": app_id(),
    }
    try:
        return jwt.encode(payload, private_key(), algorithm="RS256")
    except Exception as exc:
        # Broad on purpose: PyJWT raises several unrelated types for a bad key,
        # and none of their messages name the setting that is wrong.
        raise GitHubAppError(
            "GITHUB_APP_PRIVATE_KEY is not a usable RSA private key. Provide the "
            "PEM GitHub issued for the App, or its base64."
        ) from exc


def _headers(bearer: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {bearer}",
        "Accept": _ACCEPT,
        "X-GitHub-Api-Version": _API_VERSION,
    }


async def installation_id_for(owner: str, repo: str) -> int:
    """Which installation, if any, covers ``owner/repo``.

    A 404 is the ordinary answer for "the App is not installed there", so it is
    reported as that rather than as a transport failure.
    """
    url = f"{API_ROOT}/repos/{owner}/{repo}/installation"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(url, headers=_headers(app_jwt()))

    if response.status_code == 404:
        raise GitHubAppError(
            f"The ArchGuard GitHub App is not installed on {owner}/{repo}. "
            "Install it on that repository to analyse it."
        )
    if response.status_code != 200:
        raise GitHubAppError(
            f"GitHub refused the installation lookup for {owner}/{repo} "
            f"(HTTP {response.status_code})."
        )

    payload = response.json()
    identifier = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(identifier, int):
        raise GitHubAppError(
            f"GitHub's installation lookup for {owner}/{repo} carried no id."
        )
    return identifier


async def mint_installation_token(
    installation_id: int, *, repositories: list[str] | None = None
) -> InstallationToken:
    """Exchange the App JWT for an installation token.

    ``repositories`` narrows the token to the repositories actually being
    analysed. An installation may cover many, and a token that can read all of
    them is more authority than one clone needs.
    """
    url = f"{API_ROOT}/app/installations/{installation_id}/access_tokens"
    body: dict[str, object] = {}
    if repositories:
        body["repositories"] = repositories

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(url, headers=_headers(app_jwt()), json=body)

    if response.status_code not in (200, 201):
        raise GitHubAppError(
            f"GitHub refused an installation token for installation "
            f"{installation_id} (HTTP {response.status_code})."
        )

    payload = response.json()
    token = payload.get("token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise GitHubAppError("GitHub's token response carried no token.")

    expires_at = _parse_expiry(payload.get("expires_at"))
    logger.info(
        "Minted an installation token for installation %s, valid for %ds",
        installation_id,
        int(expires_at - time.time()),
    )
    return InstallationToken(token=token, expires_at=expires_at)


def _parse_expiry(raw: object) -> float:
    """GitHub's ``expires_at`` as a timestamp, falling back to the documented hour.

    Treating an unparseable value as already-expired would re-mint on every
    clone; treating it as long-lived would let a dead token through. The
    documented lifetime plus the refresh margin absorbs the difference.
    """
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            logger.warning("Unparseable expires_at from GitHub; assuming one hour")
    return time.time() + TOKEN_ASSUMED_LIFETIME_SECONDS


#: installation id -> token. Process-local on purpose: these are short lived,
#: and putting them in Redis would persist a credential to save a round trip.
_CACHE: dict[int, InstallationToken] = {}


async def token_for_repository(owner: str, repo: str) -> str:
    """A usable installation token for ``owner/repo``."""
    installation_id = await installation_id_for(owner, repo)
    cached = _CACHE.get(installation_id)
    if cached is not None and cached.is_usable():
        return cached.token

    minted = await mint_installation_token(installation_id, repositories=[repo])
    _CACHE[installation_id] = minted
    return minted.token


def forget_cached_tokens() -> None:
    """Drop every cached token. For tests, and for a key rotation."""
    _CACHE.clear()
