"""Job progress, published where another process can read it.

Progress lived in ``AnalysisJob.progress_messages`` -- a list on an object in
the web process's memory. That works for exactly one deployment shape: a single
web process that also runs the analysis. It cannot survive a restart, it cannot
be read by a second replica, and once the analysis moves to a worker it cannot
be read at all.

A Redis list per job, with a pub/sub channel alongside it, fixes all three. The
list is the record -- a client connecting late replays everything from the
start -- and the channel is only a doorbell, so a missed notification costs a
polling interval rather than a message.

An in-process fallback is kept for local development without Redis. The
production config check refuses to start without ``REDIS_URL``, so the fallback
cannot be what production is quietly running on.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, cast

import redis

from archguard.redis_client import get_redis

logger = logging.getLogger(__name__)

#: Long enough to read a finished job's history on the results page, short
#: enough that a busy instance does not accumulate progress for jobs nobody
#: will look at again.
PROGRESS_TTL_SECONDS = 3600

#: A job that emits more than this is looping. Trimming keeps one runaway job
#: from filling Redis, and the messages worth reading are the recent ones.
MAX_MESSAGES = 500

_LOCAL: dict[str, list[dict[str, Any]]] = {}
_LOCAL_MAX_JOBS = 200
_LOCAL_LOCK = threading.Lock()


def _key(job_id: str) -> str:
    return f"job:{job_id}:progress"


def _channel(job_id: str) -> str:
    return f"job:{job_id}"


def publish(job_id: str, event: dict[str, Any]) -> None:
    """Append an event to a job's progress and wake anyone streaming it.

    Never raises. Losing a progress message must not fail the analysis that
    produced it -- the run is the product, the commentary is not.
    """
    event = {"ts": time.time(), **event}
    payload = json.dumps(event, default=str)

    client = get_redis()
    if client is not None:
        try:
            pipe = client.pipeline()
            pipe.rpush(_key(job_id), payload)
            pipe.ltrim(_key(job_id), -MAX_MESSAGES, -1)
            pipe.expire(_key(job_id), PROGRESS_TTL_SECONDS)
            pipe.publish(_channel(job_id), payload)
            pipe.execute()
            return
        except redis.RedisError as exc:
            logger.warning("Could not publish progress for job %s: %s", job_id, exc)

    with _LOCAL_LOCK:
        if job_id not in _LOCAL and len(_LOCAL) >= _LOCAL_MAX_JOBS:
            _LOCAL.pop(next(iter(_LOCAL)))
        _LOCAL.setdefault(job_id, []).append(event)
        del _LOCAL[job_id][:-MAX_MESSAGES]


def read(job_id: str, start: int = 0) -> list[dict[str, Any]]:
    """Events from *start* onward, so a stream can resume where it left off."""
    client = get_redis()
    if client is not None:
        try:
            # redis-py types its sync client's returns as ``Awaitable[T] | T``
            # because the sync and async clients share a base class. This is
            # the sync client, so the value is never awaitable at runtime.
            raw = cast(list[Any], client.lrange(_key(job_id), start, -1))
        except redis.RedisError as exc:
            logger.warning("Could not read progress for job %s: %s", job_id, exc)
            return []
        out: list[dict[str, Any]] = []
        for item in raw:
            try:
                out.append(json.loads(item))
            except (TypeError, ValueError):
                logger.warning("Skipping unparseable progress entry for %s", job_id)
        return out

    with _LOCAL_LOCK:
        return list(_LOCAL.get(job_id, [])[start:])


def clear(job_id: str) -> None:
    """Drop a job's progress. Used by tests and by workspace eviction."""
    client = get_redis()
    if client is not None:
        try:
            client.delete(_key(job_id))
        except redis.RedisError:
            logger.warning("Could not clear progress for job %s", job_id, exc_info=True)
    with _LOCAL_LOCK:
        _LOCAL.pop(job_id, None)


def reset() -> None:
    """Forget every job's progress, on whichever backend is in use. For tests."""
    with _LOCAL_LOCK:
        _LOCAL.clear()
    client = get_redis()
    if client is None:
        return
    try:
        keys = list(client.scan_iter(match="job:*:progress", count=500))
        if keys:
            client.delete(*keys)
    except redis.RedisError:
        logger.warning("Could not clear progress keys", exc_info=True)
