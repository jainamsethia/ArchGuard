"""Per-client rate limiting.

Backed by Redis so a limit actually holds: the previous store was a process-local
``TTLCache``, so every deploy reset every counter and a second instance shared
none of them -- a caller could rotate between instances, or simply wait for a
restart, to get a fresh budget on the endpoints that cost money.

The algorithm is a fixed window (``INCR`` plus ``EXPIRE`` on first hit) rather
than the sliding window the dict held. A sliding window needs the full set of
timestamps per client, which in Redis means a sorted set and three round trips;
the fixed window needs one pipeline and cannot grow without bound. The cost is
that a caller can send up to 2x the limit across a window boundary, which for a
control sized to stop abuse rather than to meter billing is the right trade.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

import redis
from fastapi import HTTPException, Request, Response, status

from archguard.dashboard._auth import _real_client_ip
from archguard.redis_client import get_redis

logger = logging.getLogger(__name__)

RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX_REQUESTS = 50

#: LLM endpoints cost money per call, so they get their own, tighter budget.
LLM_RATE_LIMIT_MAX_REQUESTS = 30

_KEY_PREFIX = "ratelimit"


# ---------------------------------------------------------------------------
# In-process fallback, for running without Redis in development and tests.
# The startup check refuses this when ENVIRONMENT=production.
# ---------------------------------------------------------------------------

_LOCAL_LOCK = threading.Lock()
_LOCAL: dict[str, deque[float]] = {}
#: Bounded so the fallback cannot become the unbounded leak that the previous
#: cachetools-ImportError fallback was.
_LOCAL_MAX_CLIENTS = 10_000


def _local_hit(bucket: str, client_ip: str, limit: int) -> tuple[int, bool]:
    """Sliding-window count for the no-Redis path. Returns (used, exceeded)."""
    key = f"{bucket}:{client_ip}"
    now = time.time()
    with _LOCAL_LOCK:
        if key not in _LOCAL:
            if len(_LOCAL) >= _LOCAL_MAX_CLIENTS:
                # Drop the coldest half rather than grow without bound.
                for stale in sorted(_LOCAL, key=lambda k: _LOCAL[k][-1] if _LOCAL[k] else 0)[
                    : _LOCAL_MAX_CLIENTS // 2
                ]:
                    del _LOCAL[stale]
            _LOCAL[key] = deque()
        history = _LOCAL[key]
        while history and history[0] < now - RATE_LIMIT_WINDOW:
            history.popleft()
        if len(history) >= limit:
            return len(history), True
        history.append(now)
        return len(history), False


def _redis_hit(
    client: redis.Redis, bucket: str, client_ip: str, limit: int
) -> tuple[int, bool]:
    """Fixed-window count in Redis. Returns (used, exceeded)."""
    # Window number in the key, so the counter rolls over on its own and the
    # TTL is only a safety net against orphaned keys.
    window = int(time.time()) // RATE_LIMIT_WINDOW
    key = f"{_KEY_PREFIX}:{bucket}:{client_ip}:{window}"
    pipe = client.pipeline()
    pipe.incr(key)
    pipe.expire(key, RATE_LIMIT_WINDOW * 2)
    used = int(pipe.execute()[0])
    return used, used > limit


def _check(request: Request, response: Response | None, bucket: str, limit: int) -> None:
    if response is None:
        response = Response()
    client_ip = _real_client_ip(request)

    client = get_redis()
    if client is not None:
        try:
            used, exceeded = _redis_hit(client, bucket, client_ip, limit)
        except redis.RedisError as exc:
            # Fail open, loudly. Refusing every request because the rate-limit
            # store is unreachable turns a Redis blip into a full outage, and
            # the limiter exists to bound abuse, not to gate correctness.
            logger.exception("Rate-limit store unavailable, allowing request: %s", exc)
            return
    else:
        used, exceeded = _local_hit(bucket, client_ip, limit)

    remaining = max(0, limit - used)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(int(time.time()) + RATE_LIMIT_WINDOW)

    if exceeded:
        response.headers["Retry-After"] = str(RATE_LIMIT_WINDOW)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
            headers={"Retry-After": str(RATE_LIMIT_WINDOW)},
        )


def rate_limiter(request: Request, response: Response = None) -> None:  # type: ignore[assignment]
    """General per-IP budget for the API."""
    _check(request, response, "general", RATE_LIMIT_MAX_REQUESTS)


def _llm_rate_limit(request: Request, response: Response = None) -> None:  # type: ignore[assignment]
    """Tighter budget for endpoints that spend money per call."""
    _check(request, response, "llm", LLM_RATE_LIMIT_MAX_REQUESTS)


def reset_rate_limits() -> None:
    """Forget every recorded hit, on whichever backend is in use.

    For tests: without it, the first test to spend a client's budget makes
    every later test in the same file see a 429, and the order they happen to
    run in decides which ones pass.
    """
    with _LOCAL_LOCK:
        _LOCAL.clear()
    client = get_redis()
    if client is None:
        return
    try:
        keys = list(client.scan_iter(match=f"{_KEY_PREFIX}:*", count=500))
        if keys:
            client.delete(*keys)
    except redis.RedisError as exc:
        logger.warning("Could not clear rate-limit keys: %s", exc)
