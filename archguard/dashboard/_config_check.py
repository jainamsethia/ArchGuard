"""Refuse to start a production instance that is one env var from a breach.

Each check here corresponds to a misconfiguration that produces *no error* --
only wrong behaviour, quietly, in the direction of less safety. That is what
makes them worth a startup gate rather than a log line: a warning about a
setting nobody reads is indistinguishable from no warning at all.

The gate runs only when ``ENVIRONMENT=production``. Development is expected to
be missing most of this, and a check that made ``make dev`` fail would be
turned off within a week.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

#: Shannon-ish floor rather than a real strength estimate. 32 bytes is what
#: `secrets.token_hex(32)` produces as 64 hex characters, and anything much
#: shorter is a passphrase someone typed.
MIN_SECRET_CHARS = 32


class ConfigurationError(RuntimeError):
    """A production configuration that must not be served."""


def is_production() -> bool:
    return os.environ.get("ENVIRONMENT", "").lower() == "production"


def _get(name: str) -> str:
    return os.environ.get(name, "").strip()


def _check_cors(problems: list[str]) -> None:
    """``ALLOWED_ORIGINS=*`` with credentials is a same-origin policy switched off.

    Starlette's CORSMiddleware, given ``"*"`` and ``allow_credentials=True``,
    echoes back whichever Origin the request carried. Every authenticated
    endpoint then answers any site on the internet, with the user's session
    cookie attached. Nothing validated the variable, so this was one typo away.
    """
    raw = _get("ALLOWED_ORIGINS")
    if not raw:
        problems.append(
            "ALLOWED_ORIGINS is not set. In production it must name your exact "
            "origins, comma-separated -- the development default includes "
            "localhost, which is not what a deployed site should accept."
        )
        return
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if "*" in origins:
        problems.append(
            "ALLOWED_ORIGINS contains '*'. Combined with credentialed CORS this "
            "makes every authenticated endpoint readable cross-origin by any "
            "site. List your origins explicitly."
        )
    for origin in origins:
        if origin.startswith("http://") and "localhost" not in origin:
            problems.append(
                f"ALLOWED_ORIGINS contains the plaintext origin {origin!r}. "
                "A session cookie sent to it travels unencrypted."
            )


def _check_secrets(problems: list[str]) -> None:
    session_secret = _get("SESSION_SECRET")
    if not session_secret:
        problems.append(
            "SESSION_SECRET is not set. It is the HMAC key for session cookies; "
            'generate one with `python -c "import secrets; '
            'print(secrets.token_hex(32))"`.'
        )
    elif len(session_secret) < MIN_SECRET_CHARS:
        problems.append(
            f"SESSION_SECRET is only {len(session_secret)} characters. Use at "
            f"least {MIN_SECRET_CHARS}; a guessable one lets anyone forge a "
            "session for any account."
        )
    elif session_secret == _get("ARCHGUARD_DASHBOARD_TOKEN"):
        problems.append(
            "SESSION_SECRET and ARCHGUARD_DASHBOARD_TOKEN are the same value. "
            "They were one secret once, which meant rotating the operator "
            "credential signed out every user, and anyone who learned it could "
            "forge any session. Keep them distinct."
        )


def _check_oauth(problems: list[str]) -> None:
    """No sign-in configured means nobody can sign in, and nothing says so.

    The local-development fallback in ``_identity`` is gated on this being
    absent, so a production instance reaching that branch is exactly what this
    check exists to prevent -- the docstring there promises this gate holds.
    """
    from archguard.dashboard import _oauth

    if not _oauth.is_configured():
        problems.append(
            "GITHUB_OAUTH_CLIENT_ID / GITHUB_OAUTH_CLIENT_SECRET are not set. "
            "Users sign in with GitHub; without an OAuth app there is no way to "
            "sign in and every data endpoint returns 401."
        )


def _check_backing_services(problems: list[str]) -> None:
    if not _get("DATABASE_URL"):
        problems.append(
            "DATABASE_URL is not set. Users, jobs, runs and suppressions live "
            "in PostgreSQL; there is no file-based fallback."
        )
    if not _get("REDIS_URL"):
        problems.append(
            "REDIS_URL is not set. Sessions, rate-limit counters and the "
            "evolution cache would be held per-process: every deploy signs all "
            "users out, and a second instance rejects the first one's cookies."
        )


def _check_proxy_configuration(problems: list[str]) -> None:
    """Without this, every request looks like it came from the proxy.

    ``_real_client_ip`` then returns the platform's own address for everyone,
    so all users share a single rate-limit bucket -- one client can lock out the
    whole service, including the endpoints that cost money per call.
    """
    if not _get("ARCHGUARD_TRUSTED_PROXY_IPS"):
        problems.append(
            "ARCHGUARD_TRUSTED_PROXY_IPS is not set. Behind a proxy every "
            "request is attributed to the proxy's IP, so all users share one "
            "rate-limit bucket. Set it to the proxy's CIDR, or '*' if the "
            "platform guarantees it is the only ingress."
        )


def _check_data_directory(problems: list[str]) -> None:
    """Probe-write, because the audit logger swallows its own failures.

    A root-owned persistent disk under a container running as uid 1000 makes
    every ``AuditLogger.log()`` fail silently and forever, by design -- audit
    logging must never crash the caller. So the only way to find out is to try
    it here, once, where failing is the point.
    """
    directory = Path(os.environ.get("ARCHGUARD_DATA_DIR", ".archguard-cache"))
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".probe-", delete=True):
            pass
    except OSError as exc:
        problems.append(
            f"The data directory {directory} is not writable ({exc}). The audit "
            "log swallows write failures by design, so this would fail silently "
            "for the life of the deployment."
        )


def _check_allow_remote(problems: list[str]) -> None:
    if _get("ARCHGUARD_DASHBOARD_ALLOW_REMOTE").lower() in ("1", "true"):
        problems.append(
            "ARCHGUARD_DASHBOARD_ALLOW_REMOTE is enabled. It disables the "
            "IP-based guard and exists for local experiments only."
        )


CHECKS = (
    _check_cors,
    _check_secrets,
    _check_oauth,
    _check_backing_services,
    _check_proxy_configuration,
    _check_data_directory,
    _check_allow_remote,
)


def validate_configuration() -> None:
    """Raise if this process must not serve production traffic.

    Every problem is collected before raising rather than failing on the first.
    Fixing a deployment one restart at a time, five minutes apart, is how a
    ten-minute configuration job becomes an afternoon.
    """
    if not is_production():
        logger.info(
            "ENVIRONMENT is not 'production'; skipping the production "
            "configuration checks."
        )
        return

    problems: list[str] = []
    for check in CHECKS:
        check(problems)

    if problems:
        numbered = "\n".join(f"  {i}. {p}" for i, p in enumerate(problems, 1))
        raise ConfigurationError(
            f"Refusing to start: {len(problems)} production configuration "
            f"problem(s) found.\n{numbered}\n"
            "Each of these produces wrong behaviour rather than an error, which "
            "is why startup stops here instead of logging a warning."
        )

    logger.info("Production configuration checks passed.")
