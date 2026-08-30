"""A suppression has to outlive the scan that produced the finding.

Suppressing something is only useful if it stays suppressed. The dashboard
clones a throwaway workspace per scan and mints a new job id each time, so
anything keyed to either is gone before it is ever consulted -- which is what
the original defect was: suppressions were written under
``suppressions-{job_id}.jsonl`` and the filter looked inside the analysed tree.

The fix for that keyed them by repository instead, in a file named after the
URL, and traded one bug for a worse one: every account that had analysed the
same public repository shared the file. Storage is now PostgreSQL, keyed by
repository *and* owner, and these tests pin what has to survive the move --
that a suppression applies to later scans, that it stops applying when it
expires, and that the analysis reads nothing from the clone.

Cross-account isolation is pinned separately, at the HTTP layer, in
tests/integration/test_suppression_tenancy.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from archguard.analysis._suppression_filter import _filter_suppressed
from archguard.suppression.models import make_violation_hash
from tests.db_fixtures import requires_postgres

REPO = "https://github.com/owner/repo"


class _V:
    """Minimal stand-in for ViolationDetail."""

    def __init__(self, module: str, layer: int, message: str):
        self.module = module
        self.layer = layer
        self.message = message


def _violations() -> list[_V]:
    return [
        _V("lib", 2, "fan_out=22 exceeds budget=10"),
        _V("tests", 2, "fan_out=14 exceeds budget=10"),
    ]


# ------------------------------------------------------- the filter itself


def test_the_filter_reads_nothing_from_the_analysed_tree(tmp_path):
    """It is handed the answer rather than looking for it.

    The clone is a fresh temp directory nobody has ever suppressed anything in,
    so any filter that consults it is either finding nothing or finding
    somebody else's file.
    """
    clone = tmp_path / "throwaway-clone"
    clone.mkdir()

    # Nothing supplied: nothing hidden, whatever is or is not on disk.
    assert len(_filter_suppressed(clone, _violations())) == 2

    hidden = {make_violation_hash("lib", 2, "fan_out=22 exceeds budget=10")}
    kept = _filter_suppressed(clone, _violations(), hidden)
    assert [v.module for v in kept] == ["tests"]

    # And the clone stayed empty -- no cache directory, no store file.
    assert list(clone.iterdir()) == []


def test_an_unrecognised_hash_hides_nothing(tmp_path):
    """A stale or foreign identity must not silently match something."""
    kept = _filter_suppressed(tmp_path, _violations(), {"not-a-real-hash"})
    assert len(kept) == 2


# --------------------------------------------------- persistence across scans


@requires_postgres
def test_a_suppression_applies_to_every_later_scan(live_db):
    """The property the whole feature rests on.

    Each scan is a new job and a new clone, so this is stored against the
    repository and the account, and looked up again by both.
    """
    import asyncio

    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario() -> tuple[set[str], set[str]]:
        async with session_scope() as session:
            user = await store.upsert_user(session, github_id=8901, login="persist")
            user_id = user.id

        async def hashes() -> set[str]:
            async with session_scope() as session:
                return await store.active_suppression_hashes(session, user_id, REPO)

        before = await hashes()

        async with session_scope() as session:
            await store.add_suppression(
                session,
                user_id=user_id,
                repo_url=REPO,
                module="lib",
                layer=2,
                violation_hash=make_violation_hash(
                    "lib", 2, "fan_out=22 exceeds budget=10"
                ),
                reason="accepted debt",
            )

        return before, await hashes()

    before, after = asyncio.run(scenario())
    target = make_violation_hash("lib", 2, "fan_out=22 exceeds budget=10")

    assert target not in before, "suppressed before anything was suppressed"
    assert target in after, "the suppression did not survive to the next lookup"

    # And it does what it is for: two later scans, each a different clone.
    assert [v.module for v in _filter_suppressed("/clone-2", _violations(), after)] == [
        "tests"
    ]
    assert [v.module for v in _filter_suppressed("/clone-3", _violations(), after)] == [
        "tests"
    ]


@requires_postgres
def test_persistence_is_not_permanence(live_db):
    """An expiry date that does not expire anything is decoration."""
    import asyncio

    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario() -> set[str]:
        async with session_scope() as session:
            user = await store.upsert_user(session, github_id=8902, login="expiring")
            user_id = user.id
            await store.add_suppression(
                session,
                user_id=user_id,
                repo_url=REPO,
                module="lib",
                layer=2,
                violation_hash=make_violation_hash(
                    "lib", 2, "fan_out=22 exceeds budget=10"
                ),
                reason="temporary",
                expires_at=datetime.now(UTC) - timedelta(days=1),
            )
        async with session_scope() as session:
            return await store.active_suppression_hashes(session, user_id, REPO)

    active = asyncio.run(scenario())
    assert active == set(), "a lapsed suppression is still hiding its violation"
    assert len(_filter_suppressed("/clone", _violations(), active)) == 2


@requires_postgres
def test_a_repository_nobody_has_scanned_has_no_suppressions(live_db):
    """The lookup must not invent a repository row as a side effect of asking."""
    import asyncio

    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario() -> set[str]:
        async with session_scope() as session:
            user = await store.upsert_user(session, github_id=8903, login="unknown-repo")
            return await store.active_suppression_hashes(
                session, user.id, "https://github.com/never/scanned"
            )

    assert asyncio.run(scenario()) == set()


@requires_postgres
def test_deleting_a_suppression_brings_its_violation_back(live_db):
    """Un-suppressing has to work, or the button is a one-way door."""
    import asyncio

    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario() -> tuple[set[str], set[str]]:
        async with session_scope() as session:
            user = await store.upsert_user(session, github_id=8904, login="undo")
            user_id = user.id
            row = await store.add_suppression(
                session,
                user_id=user_id,
                repo_url=REPO,
                module="lib",
                layer=2,
                violation_hash=make_violation_hash(
                    "lib", 2, "fan_out=22 exceeds budget=10"
                ),
                reason="temporarily hidden",
            )
            row_id = row.id

        async with session_scope() as session:
            with_it = await store.active_suppression_hashes(session, user_id, REPO)

        async with session_scope() as session:
            removed = await store.delete_suppression(session, row_id, user_id)
            assert removed is True

        async with session_scope() as session:
            return with_it, await store.active_suppression_hashes(session, user_id, REPO)

    with_it, without = asyncio.run(scenario())
    assert with_it, "the suppression was never active"
    assert without == set(), "the suppression outlived its own deletion"


@requires_postgres
def test_deleting_something_that_does_not_exist_reports_failure(live_db):
    """False rather than a silent success, so the route can answer 404."""
    import asyncio

    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario() -> bool:
        async with session_scope() as session:
            user = await store.upsert_user(session, github_id=8905, login="missing")
            return await store.delete_suppression(
                session, "00000000-0000-0000-0000-000000000000", user.id
            )

    assert asyncio.run(scenario()) is False


# The identity a suppression is filed under is shared with the filter, so a
# record cannot be stored under one spelling and looked up under another.


@pytest.mark.parametrize(
    ("module", "layer", "message"),
    [
        ("lib", 2, "fan_out=22 exceeds budget=10"),
        ("", 0, ""),
        ("a/b", 4, "x <-> y"),
    ],
)
def test_the_hash_is_stable_for_the_same_finding(module, layer, message):
    assert make_violation_hash(module, layer, message) == make_violation_hash(
        module, layer, message
    )


def test_different_findings_hash_differently():
    assert make_violation_hash("lib", 2, "a") != make_violation_hash("lib", 2, "b")
    assert make_violation_hash("lib", 2, "a") != make_violation_hash("lib", 3, "a")
    assert make_violation_hash("lib", 2, "a") != make_violation_hash("app", 2, "a")
