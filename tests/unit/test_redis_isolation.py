"""The Redis half of test isolation.

PostgreSQL has been truncated between tests since the database landed. Redis
never was, and it holds the state that decides what a request *does* rather
than merely what it returns: per-user locks, rate-limit counters, sessions,
job progress. A row left behind changes an assertion; a lock left behind
changes a code path, which is why the failures it caused read as impossible --
a route answering 409 in a test that set up no conflict, a timeout that never
fired.

Tested through the helper rather than by leaving a key in one test and looking
for it in the next. That would be a test whose meaning depends on the order the
two halves run in, and the suite randomises order by default, so it would pass
vacuously as often as not.
"""

from __future__ import annotations

import pytest


def _redis_or_skip():
    from archguard.redis_client import get_redis

    client = get_redis()
    if client is None:
        pytest.skip("REDIS_URL is not configured")
    return client


def test_the_flush_clears_every_namespace_the_app_writes():
    from tests.db_fixtures import _REDIS_NAMESPACES, _flush_app_redis

    client = _redis_or_skip()
    planted = [f"{prefix}:isolation-probe" for prefix in _REDIS_NAMESPACES]
    for key in planted:
        client.set(key, "1", ex=120)

    assert all(client.exists(k) for k in planted), "the probe keys were not written"

    _flush_app_redis()

    survivors = [k for k in planted if client.exists(k)]
    assert survivors == [], f"these namespaces are not cleared between tests: {survivors}"


def test_the_flush_leaves_keys_it_does_not_own_alone():
    """The test Redis is usually the developer's own, on database 0.

    A FLUSHDB would be simpler and would also sign them out of the dashboard
    they had open and discard the job progress they were watching. So the
    clear is by namespace, and this pins that it stays that way.
    """
    from tests.db_fixtures import _flush_app_redis

    client = _redis_or_skip()
    foreign = "some-other-tool:keep-me"
    client.set(foreign, "1", ex=120)
    try:
        _flush_app_redis()
        assert client.exists(foreign), (
            "the between-test clear deleted a key the application does not own"
        )
    finally:
        client.delete(foreign)


def test_every_namespace_the_app_writes_is_listed():
    """The list is hand-maintained, so this is what stops it rotting.

    A new Redis-backed feature that invents its own prefix would otherwise leak
    between tests silently, and the symptom would appear somewhere unrelated
    weeks later.
    """
    import re
    from pathlib import Path

    from tests.db_fixtures import _REDIS_NAMESPACES

    root = Path(__file__).resolve().parents[2] / "archguard"
    # Matches the key builders actually in use: f"prefix:{...}" and
    # f"{_KEY_PREFIX}:{...}" paired with a _KEY_PREFIX = "..." assignment.
    literal = re.compile(r'f"([a-z][a-z_]*):\{')
    assigned = re.compile(r'_KEY_PREFIX\s*=\s*"([a-z][a-z_]*)"')

    found: set[str] = set()
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "redis" not in text.lower():
            continue
        found.update(literal.findall(text))
        found.update(assigned.findall(text))

    missing = sorted(found - set(_REDIS_NAMESPACES))
    assert missing == [], (
        f"these Redis key prefixes are written by the application but not "
        f"cleared between tests: {missing}. Add them to _REDIS_NAMESPACES in "
        f"tests/db_fixtures.py."
    )
