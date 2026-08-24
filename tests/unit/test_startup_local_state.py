"""The startup warning about process-local state must describe this process.

The warning it guards had drifted badly. It announced that "analysis jobs,
login sessions are still held in this process and are lost on restart" *even
when Redis and PostgreSQL were configured* -- by which point sessions were in
Redis (``_sessions``: "The store is Redis, not a process dict"), job records
were in PostgreSQL (``db.store.create_job``) and job progress was in Redis
(``worker.progress``). It also contradicted ``_config_check``, which tells
operators the opposite.

The comment above that warning existed to prevent exactly this -- "a blanket
warning that stayed constant while the facts changed under it is worse than
none" -- and it drifted anyway, because nothing asserted on its contents. This
is that assertion.
"""

from __future__ import annotations

from archguard.dashboard.app import process_local_state


def test_sessions_are_not_called_process_local_when_redis_is_configured() -> None:
    named = " ".join(process_local_state(redis_configured=True)).lower()
    assert "session" not in named, (
        "sessions live in Redis when REDIS_URL is set; saying otherwise tells an "
        "operator that every deploy signs all users out, which is not true"
    )


def test_job_records_are_never_called_process_local() -> None:
    # Job records live in PostgreSQL either way -- with no DATABASE_URL the app
    # raises rather than falling back to memory, so they are never local.
    for configured in (True, False):
        named = " ".join(process_local_state(redis_configured=configured)).lower()
        assert "analysis jobs" not in named


def test_the_sse_stream_tokens_are_reported_even_with_redis() -> None:
    # _cookie_auth._STREAM_TOKENS is a plain dict with no Redis path at all.
    # This is the one thing the warning should have been naming: behind a load
    # balancer a token minted by one replica does not validate on another.
    named = " ".join(process_local_state(redis_configured=True)).lower()
    assert "stream" in named


def test_the_redis_backed_stores_are_named_only_when_redis_is_absent() -> None:
    with_redis = " ".join(process_local_state(redis_configured=True)).lower()
    without = " ".join(process_local_state(redis_configured=False)).lower()

    # "job progress", not "progress": the SSE progress-stream tokens are named
    # unconditionally and would match the shorter substring.
    for store in ("session", "rate limit", "evolution", "lock", "job progress"):
        assert store in without, f"{store} falls back to memory with no Redis; say so"
        assert store not in with_redis, f"{store} is in Redis when configured"


def test_nothing_is_dropped_by_configuring_redis() -> None:
    # Configuring Redis can only shorten the list, never change what is on it.
    # A warning that swapped one set of claims for another would be how the
    # previous drift happened again.
    with_redis = set(process_local_state(redis_configured=True))
    without = set(process_local_state(redis_configured=False))
    assert with_redis <= without


def test_the_warning_is_never_empty() -> None:
    # The SSE tokens are unconditionally local, so there is always something to
    # report. An empty list would mean this check had quietly stopped checking.
    assert process_local_state(redis_configured=True)
