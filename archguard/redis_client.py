"""Redis connection management.

Holds the state that must survive a process restart but does not belong in
PostgreSQL: sessions, per-IP rate-limit counters, and short-lived caches. All
three were process-local dicts, so a deploy logged every user out, reset every
rate limit, and a second instance shared none of it.

The client here is the **synchronous** one on purpose. Every current consumer --
``rate_limiter``, ``check_token`` and the evolution routes -- is a sync FastAPI
dependency or endpoint, which FastAPI runs in a threadpool; a blocking Redis
call there costs a threadpool slot, not the event loop. The async client is
introduced when the SSE progress stream and the queue worker need it, rather
than maintaining both before anything uses the second.
"""

from __future__ import annotations

import logging
import os

import redis

logger = logging.getLogger(__name__)

REDIS_URL_ENV = "REDIS_URL"


class RedisNotConfiguredError(RuntimeError):
    """Raised when Redis is required but ``REDIS_URL`` is unset."""


class _RedisState:
    """Holds the process-wide client, without needing `global`."""

    client: redis.Redis | None = None
    checked: bool = False


_state = _RedisState()


def redis_url() -> str:
    return os.environ.get(REDIS_URL_ENV, "").strip()


def is_configured() -> bool:
    return bool(redis_url())


def get_redis() -> redis.Redis | None:
    """The shared client, or None when ``REDIS_URL`` is unset.

    Returning None rather than raising is what lets a developer run the test
    suite without a Redis server. It is emphatically not a production mode:
    the startup configuration check refuses to boot without ``REDIS_URL`` when
    ``ENVIRONMENT=production``, so the degraded path cannot be reached there by
    accident -- which is the difference between this and the ``cachetools``
    ImportError fallback it replaces, whose silent substitution of a plain dict
    turned the rate limiter into an unbounded memory leak.
    """
    if _state.client is not None:
        return _state.client

    url = redis_url()
    if not url:
        if not _state.checked:
            _state.checked = True
            logger.warning(
                "%s is not set. Sessions, rate limits and caches will be kept "
                "in this process only: they are lost on restart and not shared "
                "between instances. Acceptable for local development; the "
                "startup check refuses it in production.",
                REDIS_URL_ENV,
            )
        return None

    _state.client = redis.Redis.from_url(
        url,
        decode_responses=True,
        # A Redis that has gone away must surface as a fast error, not a hung
        # request holding a threadpool slot open.
        socket_timeout=float(os.environ.get("ARCHGUARD_REDIS_TIMEOUT", "3")),
        socket_connect_timeout=float(os.environ.get("ARCHGUARD_REDIS_TIMEOUT", "3")),
        health_check_interval=30,
    )
    logger.info("Redis client created for %s", _redact(url))
    return _state.client


def require_redis() -> redis.Redis:
    """The client, or an error naming what is missing."""
    client = get_redis()
    if client is None:
        raise RedisNotConfiguredError(
            f"{REDIS_URL_ENV} is not set and this operation requires Redis."
        )
    return client


def ping() -> bool:
    """True if Redis is configured and answering. Used by the readiness check."""
    client = get_redis()
    if client is None:
        return False
    try:
        return bool(client.ping())
    except redis.RedisError as exc:
        logger.warning("Redis ping failed: %s", exc)
        return False


def close_redis() -> None:
    """Release the connection pool. Called on application shutdown."""
    if _state.client is not None:
        _state.client.close()
        logger.info("Redis client closed")
    _state.client = None
    _state.checked = False


def _redact(url: str) -> str:
    """Strip the password before a URL reaches a log line."""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"
