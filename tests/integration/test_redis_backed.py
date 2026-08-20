"""Redis-backed state, tested against a real Redis server.

Not mocked. The point of moving these off process-local dicts is that they
survive a restart and are shared between instances, and neither property can be
demonstrated against a stand-in.

Each test asserts against both backends where it can, because the in-process
fallback is a real code path a developer runs on every day.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request

pytestmark = pytest.mark.integration

REDIS_URL = os.environ.get("REDIS_URL", "").strip()

requires_redis = pytest.mark.skipif(
    not REDIS_URL,
    reason="REDIS_URL is not set; see docs/DEVELOPMENT.md for local setup",
)


@pytest.fixture
def redis_backend(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """Point the client at the real server and start from a clean slate."""
    from archguard import redis_client
    from archguard.dashboard._rate_limit import reset_rate_limits

    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    redis_client.close_redis()
    client = redis_client.get_redis()
    assert client is not None, "REDIS_URL set but no client was created"
    reset_rate_limits()
    yield client
    reset_rate_limits()
    redis_client.close_redis()


@pytest.fixture
def local_backend(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The no-Redis path a developer without a server runs on."""
    from archguard import redis_client
    from archguard.dashboard._rate_limit import reset_rate_limits

    monkeypatch.delenv("REDIS_URL", raising=False)
    redis_client.close_redis()
    reset_rate_limits()
    yield
    reset_rate_limits()
    redis_client.close_redis()


def _request(ip: str = "203.0.113.7") -> Request:
    req = MagicMock(spec=Request)
    req.headers = {}
    req.client = MagicMock()
    req.client.host = ip
    return req


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------


@requires_redis
def test_ping_reaches_a_real_server(redis_backend: object) -> None:
    from archguard.redis_client import ping

    assert ping() is True


def test_ping_is_false_without_a_url(local_backend: None) -> None:
    """The readiness check must report Redis as down, not raise."""
    from archguard.redis_client import ping

    assert ping() is False


def test_require_redis_names_what_is_missing(local_backend: None) -> None:
    from archguard.redis_client import RedisNotConfiguredError, require_redis

    with pytest.raises(RedisNotConfiguredError, match="REDIS_URL"):
        require_redis()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


@requires_redis
def test_rate_limit_is_enforced_against_real_redis(redis_backend: object) -> None:
    from archguard.dashboard._rate_limit import RATE_LIMIT_MAX_REQUESTS, rate_limiter

    req = _request("198.51.100.10")
    for _ in range(RATE_LIMIT_MAX_REQUESTS):
        rate_limiter(req)

    with pytest.raises(HTTPException) as exc:
        rate_limiter(req)
    assert exc.value.status_code == 429


@requires_redis
def test_the_counter_actually_lives_in_redis(redis_backend: object) -> None:
    """The whole point: a second process, or this one after a restart, sees the
    same budget. A process-local dict cannot demonstrate that."""
    from archguard.dashboard._rate_limit import rate_limiter

    rate_limiter(_request("198.51.100.11"))
    keys = list(redis_backend.scan_iter(match="ratelimit:*"))  # type: ignore[attr-defined]
    assert keys, "no rate-limit key was written to Redis"
    assert any("198.51.100.11" in k for k in keys)


@requires_redis
def test_budgets_are_per_client(redis_backend: object) -> None:
    from archguard.dashboard._rate_limit import RATE_LIMIT_MAX_REQUESTS, rate_limiter

    noisy = _request("198.51.100.12")
    for _ in range(RATE_LIMIT_MAX_REQUESTS):
        rate_limiter(noisy)
    with pytest.raises(HTTPException):
        rate_limiter(noisy)

    # A different caller must be unaffected.
    rate_limiter(_request("198.51.100.13"))


@requires_redis
def test_llm_budget_is_separate_and_tighter(redis_backend: object) -> None:
    """LLM calls cost money, so they must not share the general budget."""
    from archguard.dashboard._rate_limit import (
        LLM_RATE_LIMIT_MAX_REQUESTS,
        RATE_LIMIT_MAX_REQUESTS,
        _llm_rate_limit,
        rate_limiter,
    )

    assert LLM_RATE_LIMIT_MAX_REQUESTS < RATE_LIMIT_MAX_REQUESTS

    req = _request("198.51.100.14")
    for _ in range(LLM_RATE_LIMIT_MAX_REQUESTS):
        _llm_rate_limit(req)
    with pytest.raises(HTTPException):
        _llm_rate_limit(req)

    # The general budget is untouched by LLM spending.
    rate_limiter(req)


def test_rate_limit_is_enforced_without_redis(local_backend: None) -> None:
    """The fallback still limits; it just cannot be shared."""
    from archguard.dashboard._rate_limit import RATE_LIMIT_MAX_REQUESTS, rate_limiter

    req = _request("198.51.100.20")
    for _ in range(RATE_LIMIT_MAX_REQUESTS):
        rate_limiter(req)
    with pytest.raises(HTTPException) as exc:
        rate_limiter(req)
    assert exc.value.status_code == 429


def test_the_fallback_store_is_bounded(local_backend: None) -> None:
    """The store it replaces became an unbounded, never-expiring leak keyed by
    client IP when cachetools was missing. This one cannot."""
    from archguard.dashboard import _rate_limit

    for i in range(_rate_limit._LOCAL_MAX_CLIENTS + 500):
        _rate_limit.rate_limiter(_request(f"10.0.{i // 256}.{i % 256}"))

    assert len(_rate_limit._LOCAL) <= _rate_limit._LOCAL_MAX_CLIENTS


@requires_redis
def test_a_redis_outage_fails_open_rather_than_closed(
    redis_backend: object, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Refusing every request because the rate-limit store is unreachable turns
    a Redis blip into a full outage. The limiter bounds abuse; it does not gate
    correctness."""
    import logging

    import redis as redis_pkg

    from archguard.dashboard import _rate_limit

    def _boom(*_a: object, **_k: object) -> None:
        raise redis_pkg.ConnectionError("simulated outage")

    monkeypatch.setattr(_rate_limit, "_redis_hit", _boom)

    with caplog.at_level(logging.ERROR, logger="archguard.dashboard._rate_limit"):
        _rate_limit.rate_limiter(_request("198.51.100.30"))  # must not raise

    assert "unavailable" in caplog.text.lower()


# ---------------------------------------------------------------------------
# Evolution report cache (D5)
# ---------------------------------------------------------------------------


@requires_redis
def test_evolution_report_round_trips_through_redis(redis_backend: object) -> None:
    from archguard.dashboard.routes.evolution import _evo_load, _evo_store

    report = {"snapshots": [{"sha": "abc1234", "health_score": 91.0}], "debt_velocity": -0.01}
    _evo_store("job-a", report)
    assert _evo_load("job-a") == report


@requires_redis
def test_evolution_reports_are_scoped_per_job(redis_backend: object) -> None:
    """One repository's git history must never be served as another's."""
    from archguard.dashboard.routes.evolution import _evo_load, _evo_store

    _evo_store("job-a", {"debt_velocity": 1.0})
    _evo_store("job-b", {"debt_velocity": 2.0})
    assert _evo_load("job-a") == {"debt_velocity": 1.0}
    assert _evo_load("job-b") == {"debt_velocity": 2.0}
    assert _evo_load("job-c") is None


@requires_redis
def test_evolution_cache_entries_expire(redis_backend: object) -> None:
    """Unbounded growth was the defect; a TTL is what fixes it."""
    from archguard.dashboard.routes.evolution import _evo_key, _evo_store

    _evo_store("job-ttl", {"debt_velocity": 0.0})
    ttl = redis_backend.ttl(_evo_key("job-ttl"))  # type: ignore[attr-defined]
    assert 0 < ttl <= 3600


def test_evolution_fallback_cache_is_bounded(local_backend: None) -> None:
    from archguard.dashboard.routes import evolution

    evolution._EVO_LOCAL.clear()
    for i in range(evolution._EVO_LOCAL_MAX + 50):
        evolution._evo_store(f"job-{i}", {"n": i})

    assert len(evolution._EVO_LOCAL) <= evolution._EVO_LOCAL_MAX
