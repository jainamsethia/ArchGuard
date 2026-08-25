"""Suppression storage guarantees, now that PostgreSQL holds them.

These moved here from the JSONL store's tests. Expiry and ownership are the two
properties the file store either got wrong or could not express at all, so they
are asserted against real rows rather than a fake.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from archguard.db.session import session_scope
from archguard.db.store import (
    active_violation_hashes,
    add_suppression,
    delete_suppression,
    list_suppressions,
)
from archguard.suppression.models import make_violation_hash
from tests.db_fixtures import requires_postgres

REPO = "https://github.com/example/repo"
OTHER_REPO = "https://github.com/example/other"


def _add(user_id: int, module: str, message: str, repo: str = REPO, **kw):
    async def _run():
        async with session_scope() as session:
            return await add_suppression(
                session,
                repo_url=repo,
                module=module,
                layer=2,
                violation_hash=make_violation_hash(module, 2, message),
                reason="because",
                user_id=user_id,
                **kw,
            )

    return asyncio.run(_run())


def _hashes(user_id: int, repo: str = REPO) -> set[str]:
    async def _run():
        async with session_scope() as session:
            return await active_violation_hashes(session, repo, user_id)

    return asyncio.run(_run())


@requires_postgres
def test_a_suppression_applies_to_later_runs(test_user):
    """Durability is the whole point; a re-scan must still see it."""
    _add(test_user.id, "lib", "fan_out=22 exceeds budget=10")

    assert make_violation_hash("lib", 2, "fan_out=22 exceeds budget=10") in _hashes(
        test_user.id
    )


@requires_postgres
def test_an_expired_suppression_stops_hiding_the_violation(test_user):
    """Persistence must not mean permanence.

    Expiry is applied at read time rather than by a sweep, so it takes effect
    when it expires and not whenever something next tidies up.
    """
    _add(
        test_user.id,
        "lib",
        "fan_out=22 exceeds budget=10",
        expires_at=datetime(2020, 1, 1, tzinfo=UTC),
    )

    assert _hashes(test_user.id) == set()


@requires_postgres
def test_a_suppression_expiring_later_still_applies(test_user):
    """The other half of the above, so the test is about expiry and not storage."""
    _add(
        test_user.id,
        "lib",
        "fan_out=22 exceeds budget=10",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )

    assert len(_hashes(test_user.id)) == 1


@requires_postgres
def test_suppressions_do_not_leak_between_users(test_user, second_user):
    """What the repository-keyed file could not express."""
    _add(second_user.id, "lib", "fan_out=22 exceeds budget=10")

    assert len(_hashes(second_user.id)) == 1
    assert _hashes(test_user.id) == set()


@requires_postgres
def test_suppressions_do_not_leak_between_repositories(test_user):
    _add(test_user.id, "lib", "fan_out=22 exceeds budget=10", repo=REPO)

    assert len(_hashes(test_user.id, REPO)) == 1
    assert _hashes(test_user.id, OTHER_REPO) == set()


@requires_postgres
def test_delete_is_scoped_to_the_owner(test_user, second_user):
    """A suppression id is a bare UUID in a request body.

    Fetching by id alone would let anyone holding one delete another user's row.
    """
    mine = _add(test_user.id, "lib", "fan_out=22 exceeds budget=10")

    async def _delete_as(user_id: int) -> bool:
        async with session_scope() as session:
            return await delete_suppression(session, mine.id, user_id)

    assert asyncio.run(_delete_as(second_user.id)) is False
    assert len(_hashes(test_user.id)) == 1, "it must still be there"

    assert asyncio.run(_delete_as(test_user.id)) is True
    assert _hashes(test_user.id) == set()


@requires_postgres
def test_listing_is_scoped_to_the_owner(test_user, second_user):
    _add(second_user.id, "lib", "fan_out=22 exceeds budget=10")

    async def _list(user_id: int):
        async with session_scope() as session:
            return await list_suppressions(session, REPO, user_id)

    assert len(asyncio.run(_list(second_user.id))) == 1
    assert asyncio.run(_list(test_user.id)) == []
