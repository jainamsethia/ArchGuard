"""One-at-a-time locks, held in Redis so they hold across replicas.

Used where an endpoint's cost is large enough that a caller must not be able to
have several of them running at once. A per-process lock would not do: with two
web instances behind a load balancer, a caller gets one concurrent operation
per instance, which is the same unbounded problem with extra steps.

Every lock carries a TTL. A process killed while holding one must not lock a
user out of a feature until someone notices -- an expired lock costs at worst a
second concurrent operation, a stuck one costs the feature.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Iterator

import redis

from archguard.redis_client import get_redis

logger = logging.getLogger(__name__)

_LOCAL: dict[str, float] = {}
_LOCAL_LOCK = threading.Lock()


class LockHeld(RuntimeError):
    """Someone already holds this lock."""


def _acquire(key: str, ttl: int) -> bool:
    client = get_redis()
    if client is not None:
        try:
            # SET NX EX is atomic, which a get-then-set is not: two requests
            # arriving together would both read "free" and both proceed.
            return bool(client.set(key, "1", nx=True, ex=ttl))
        except redis.RedisError:
            # Fail open. Refusing every request because the lock store is down
            # turns a Redis blip into an outage of the feature, and the thing
            # being prevented is expensive rather than dangerous.
            logger.warning("Could not acquire lock %s; proceeding", key, exc_info=True)
            return True

    now = time.time()
    with _LOCAL_LOCK:
        for expired in [k for k, exp in _LOCAL.items() if exp <= now]:
            del _LOCAL[expired]
        if key in _LOCAL:
            return False
        _LOCAL[key] = now + ttl
        return True


def _release(key: str) -> None:
    client = get_redis()
    if client is not None:
        try:
            client.delete(key)
        except redis.RedisError:
            # The TTL is the backstop, so a failed release costs a wait rather
            # than a permanent lock.
            logger.warning("Could not release lock %s", key, exc_info=True)
    with _LOCAL_LOCK:
        _LOCAL.pop(key, None)


@contextlib.contextmanager
def single_flight(name: str, user_id: int, ttl: int) -> Iterator[None]:
    """Hold a per-user lock for the duration, or raise ``LockHeld``.

    Named per user rather than globally: one person running an expensive
    operation must not stop everyone else running theirs.
    """
    key = f"lock:{name}:{user_id}"
    if not _acquire(key, ttl):
        raise LockHeld(key)
    try:
        yield
    finally:
        _release(key)


def reset_locks() -> None:
    """Release every lock, on whichever backend is in use. For tests."""
    with _LOCAL_LOCK:
        _LOCAL.clear()
    client = get_redis()
    if client is None:
        return
    try:
        keys = list(client.scan_iter(match="lock:*", count=500))
        if keys:
            client.delete(*keys)
    except redis.RedisError:
        logger.warning("Could not clear locks", exc_info=True)
