"""Session storage, keyed to a user rather than to a shared secret.

The cookie format is unchanged -- ``<session_id>.<hmac>`` -- but two things
about it are different, and both matter.

The HMAC key is ``SESSION_SECRET``, not ``ARCHGUARD_DASHBOARD_TOKEN``. They had
been the same value, which meant the operator credential and every browser
session shared one secret: rotating the ops token logged out every user, and
anyone who learned the ops token could forge any session.

The store is Redis, not a process dict. An in-memory store cannot survive a
restart and cannot be shared between replicas, so every deploy signed everyone
out and a second instance rejected the first instance's cookies. A bounded
in-process fallback is kept for local development only, and the production
config check refuses to start without ``REDIS_URL``.

What a session record holds is a user id. That is the whole point: knowing
*who* is asking is what makes per-user isolation expressible at all.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from typing import cast

import redis

from archguard.redis_client import get_redis

logger = logging.getLogger(__name__)

COOKIE_NAME = "archguard_session"
_KEY_PREFIX = "session"

#: Local-development fallback only. Bounded so a loop cannot exhaust memory.
_LOCAL: dict[str, tuple[int, float]] = {}
_LOCAL_MAX = 10_000
_LOCAL_LOCK = threading.Lock()


class SessionSecretMissingError(RuntimeError):
    """Raised when a session operation is attempted with no SESSION_SECRET."""


def ttl_seconds() -> int:
    return int(os.environ.get("ARCHGUARD_SESSION_COOKIE_TTL", "86400"))


def session_secret() -> str:
    """The HMAC key for session cookies.

    Deliberately not defaulting to ``ARCHGUARD_DASHBOARD_TOKEN``: a silent
    fallback would quietly recreate the shared-secret coupling this module
    exists to break, and it would do so in exactly the deployment that forgot to
    set the variable.
    """
    secret = os.environ.get("SESSION_SECRET", "").strip()
    if not secret:
        raise SessionSecretMissingError(
            "SESSION_SECRET is not set. Generate one with "
            '`python -c "import secrets; print(secrets.token_hex(32))"`.'
        )
    return secret


def _sign(secret: str, session_id: str) -> str:
    return hmac.new(secret.encode(), session_id.encode(), hashlib.sha256).hexdigest()


def _key(session_id: str) -> str:
    return f"{_KEY_PREFIX}:{session_id}"


def issue(user_id: int) -> str:
    """Create a session for a user and return the signed cookie value."""
    secret = session_secret()
    session_id = secrets.token_hex(32)
    ttl = ttl_seconds()

    client = get_redis()
    if client is not None:
        try:
            client.set(_key(session_id), str(user_id), ex=ttl)
        except redis.RedisError:
            # Failing open here would issue a cookie no lookup can resolve, so
            # the user would appear signed in and then be rejected by the next
            # request. Fail loudly instead.
            logger.exception("Could not store session in Redis")
            raise
    else:
        _local_put(session_id, user_id, ttl)

    return f"{session_id}.{_sign(secret, session_id)}"


def resolve(cookie_value: str) -> int | None:
    """Return the user id a cookie identifies, or None.

    Signature first, lookup second. Verifying the HMAC before touching the store
    means a forged or malformed cookie never becomes a Redis round trip, so the
    cookie cannot be used to probe which session ids exist.
    """
    if not cookie_value:
        return None
    try:
        secret = session_secret()
    except SessionSecretMissingError:
        return None

    session_id, _, sig = cookie_value.partition(".")
    if not sig or not hmac.compare_digest(_sign(secret, session_id), sig):
        return None

    client = get_redis()
    if client is not None:
        try:
            # Sync client; redis-py's shared base class is what makes the
            # annotation ``Awaitable[T] | T``.
            raw = cast("bytes | str | None", client.get(_key(session_id)))
        except redis.RedisError:
            # Fail closed. A session store that cannot answer is not a licence
            # to treat an unverified cookie as a signed-in user.
            logger.exception("Could not read session from Redis")
            return None
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.warning("Session %s holds a non-numeric user id", session_id[:8])
            return None

    return _local_get(session_id)


def revoke(cookie_value: str) -> None:
    """Drop a session. Signing out must work even when the cookie is malformed."""
    session_id = cookie_value.partition(".")[0]
    if not session_id:
        return
    client = get_redis()
    if client is not None:
        try:
            client.delete(_key(session_id))
        except redis.RedisError:
            logger.exception("Could not delete session from Redis")
    with _LOCAL_LOCK:
        _LOCAL.pop(session_id, None)


def reset_sessions() -> None:
    """Forget every session, on whichever backend is in use. For tests."""
    with _LOCAL_LOCK:
        _LOCAL.clear()
    client = get_redis()
    if client is None:
        return
    try:
        keys = list(client.scan_iter(match=f"{_KEY_PREFIX}:*", count=500))
        if keys:
            client.delete(*keys)
    except redis.RedisError:
        logger.warning("Could not clear session keys", exc_info=True)


def _local_put(session_id: str, user_id: int, ttl: int) -> None:
    now = time.time()
    with _LOCAL_LOCK:
        expired = [sid for sid, (_uid, exp) in _LOCAL.items() if exp <= now]
        for sid in expired:
            del _LOCAL[sid]
        if len(_LOCAL) >= _LOCAL_MAX:
            # Oldest expiry first, so the entry dropped is the one closest to
            # being worthless anyway.
            oldest = min(_LOCAL, key=lambda sid: _LOCAL[sid][1])
            del _LOCAL[oldest]
        _LOCAL[session_id] = (user_id, now + ttl)


def _local_get(session_id: str) -> int | None:
    with _LOCAL_LOCK:
        entry = _LOCAL.get(session_id)
        if entry is None:
            return None
        user_id, expires_at = entry
        if expires_at <= time.time():
            del _LOCAL[session_id]
            return None
        return user_id
