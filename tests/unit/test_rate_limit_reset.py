"""`reset_rate_limits` must still isolate tests, while doing far less work.

It is autouse for every unit test, and it ran a full Redis keyspace SCAN plus a
DELETE each time -- twice, because the fixture called it twice. 843 unit tests
meant 1,686 round trips, and all but a handful of them found nothing to delete,
because most tests never touch the rate limiter at all.

That was merely wasteful while Redis was healthy. When Redis went away it
became the difference between a 3.5-minute suite and a 48-minute one: every
test paid the 3-second connection timeout before failing open.

The optimisation is a flag: if nothing has been written to Redis since the last
reset, there is nothing to clear. These tests exist to prove that the isolation
the fixture provides is unchanged -- what is skipped is only ever work that
would have found nothing.
"""

from __future__ import annotations

import pytest

from archguard.dashboard import _rate_limit


@pytest.fixture(autouse=True)
def _clean_module_state():
    """Each test here starts from a known flag, and leaves one behind."""
    _rate_limit._redis_touched = False
    yield
    _rate_limit._redis_touched = False


class _SpyRedis:
    """Records what the reset asks Redis to do."""

    def __init__(self, keys: list[str] | None = None) -> None:
        self.keys = keys or []
        self.scans = 0
        self.deleted: list[str] = []

    def scan_iter(self, match=None, count=None):
        self.scans += 1
        return iter(self.keys)

    def delete(self, *keys):
        self.deleted.extend(keys)
        return len(keys)

    def pipeline(self):
        return _SpyPipeline(self)


class _SpyPipeline:
    def __init__(self, client: _SpyRedis) -> None:
        self.client = client

    def incr(self, key):
        self.client.keys.append(key)

    def expire(self, key, ttl):
        pass

    def execute(self):
        return [1, True]


def test_the_local_buckets_are_always_cleared(monkeypatch):
    """The in-process half is the isolation most tests actually rely on, and it
    is cheap, so it happens unconditionally."""
    monkeypatch.setattr(_rate_limit, "get_redis", lambda: None)
    _rate_limit._LOCAL["ratelimit:general:1.2.3.4"] = None

    _rate_limit.reset_rate_limits()

    assert _rate_limit._LOCAL == {}


def test_redis_is_not_touched_when_nothing_was_written(monkeypatch):
    """The saving. A test that never exercised the rate limiter must not cause
    a connection, let alone a keyspace scan."""
    spy = _SpyRedis()
    calls = []
    monkeypatch.setattr(_rate_limit, "get_redis", lambda: calls.append(1) or spy)

    _rate_limit.reset_rate_limits()

    assert calls == [], "reset opened a Redis connection for nothing"
    assert spy.scans == 0


def test_redis_keys_are_cleared_after_a_recorded_hit(monkeypatch):
    """The isolation. Once a hit has been recorded, the next reset must remove
    it -- otherwise one test's budget leaks into the next and a 429 assertion
    depends on running order.
    """
    spy = _SpyRedis(keys=["ratelimit:general:1.2.3.4:9999"])
    monkeypatch.setattr(_rate_limit, "get_redis", lambda: spy)

    _rate_limit._redis_hit(spy, "general", "1.2.3.4", limit=50)
    _rate_limit.reset_rate_limits()

    assert spy.scans == 1, "the recorded hit was not scanned for"
    assert spy.deleted, "the recorded hit was not deleted"


def test_the_flag_is_lowered_so_the_next_reset_is_cheap_again(monkeypatch):
    spy = _SpyRedis(keys=["ratelimit:general:1.2.3.4:9999"])
    monkeypatch.setattr(_rate_limit, "get_redis", lambda: spy)

    _rate_limit._redis_hit(spy, "general", "1.2.3.4", limit=50)
    _rate_limit.reset_rate_limits()
    _rate_limit.reset_rate_limits()

    assert spy.scans == 1, "the second reset scanned again with nothing written"


def test_a_hit_after_a_reset_is_cleared_by_the_next_one(monkeypatch):
    """The sequence a real suite produces: reset, test writes, reset."""
    spy = _SpyRedis()
    monkeypatch.setattr(_rate_limit, "get_redis", lambda: spy)

    _rate_limit.reset_rate_limits()
    _rate_limit._redis_hit(spy, "llm", "5.6.7.8", limit=30)
    _rate_limit.reset_rate_limits()

    assert spy.scans == 1
    assert spy.deleted, "the hit written between resets survived"


def test_a_redis_failure_during_reset_does_not_fail_the_test(monkeypatch):
    """Rate limiting fails open by design; clearing it must too, or an
    unrelated Redis blip fails every test in the suite at setup."""
    class _Broken(_SpyRedis):
        def scan_iter(self, match=None, count=None):
            import redis

            raise redis.RedisError("connection lost")

    broken = _Broken()
    monkeypatch.setattr(_rate_limit, "get_redis", lambda: broken)
    _rate_limit._redis_hit(broken, "general", "1.2.3.4", limit=50)

    _rate_limit.reset_rate_limits()  # must not raise
