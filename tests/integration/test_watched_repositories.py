"""Persistence for watched repositories (P3-1).

A watch is what turns a one-off analysis into a scheduled one. It is owned by
the user rather than the instance -- two people watching the same repository is
ordinary, and every other row in this schema is already scoped that way.

``last_seen_sha`` is the part that makes a scheduled scan affordable. ADR-009
measured that re-analysing an unchanged repository costs ~4s in a warm worker
and concluded file-level hashing is not worth wiring; the gate that is worth it
is the commit. These tests pin the storage half of that: the SHA is recorded,
and the poll timestamp is written even when nothing changed, so a watcher that
has quietly stopped working shows as a stale timestamp rather than as silence.
"""

from __future__ import annotations

import pytest

from tests.db_fixtures import requires_postgres

_URL = "https://github.com/pallets/flask.git"
_OTHER = "https://github.com/psf/requests.git"


async def _a_user(session, github_id: int, login: str):
    from archguard.db.store import upsert_user

    return await upsert_user(session, github_id=github_id, login=login)


@requires_postgres
@pytest.mark.asyncio
async def test_watching_is_idempotent(live_db: str) -> None:
    """The caller is a toggle; re-watching must not raise or duplicate."""
    from archguard.db import session_scope
    from archguard.db.store import is_watched, watch_repository

    async with session_scope() as s:
        user = await _a_user(s, 91001, "watcher-a")
        await watch_repository(s, user.id, _URL)
        await watch_repository(s, user.id, _URL)
        uid = user.id

    async with session_scope() as s:
        assert await is_watched(s, uid, _URL) is True


@requires_postgres
@pytest.mark.asyncio
async def test_a_watch_belongs_to_one_user_only(live_db: str) -> None:
    """Per-user isolation: one person watching must not enrol another."""
    from archguard.db import session_scope
    from archguard.db.store import is_watched, list_watched, watch_repository

    async with session_scope() as s:
        owner = await _a_user(s, 91002, "watcher-b")
        stranger = await _a_user(s, 91003, "watcher-c")
        await watch_repository(s, owner.id, _URL)
        owner_id, stranger_id = owner.id, stranger.id

    async with session_scope() as s:
        assert await is_watched(s, owner_id, _URL) is True
        assert await is_watched(s, stranger_id, _URL) is False
        assert await list_watched(s, stranger_id) == []


@requires_postgres
@pytest.mark.asyncio
async def test_two_users_can_watch_the_same_repository(live_db: str) -> None:
    from archguard.db import session_scope
    from archguard.db.store import all_watched, watch_repository

    async with session_scope() as s:
        a = await _a_user(s, 91004, "watcher-d")
        b = await _a_user(s, 91005, "watcher-e")
        await watch_repository(s, a.id, _OTHER)
        await watch_repository(s, b.id, _OTHER)
        ids = {a.id, b.id}

    async with session_scope() as s:
        watching_it = {w["user_id"] for w in await all_watched(s) if w["repo_url"] == _OTHER}
        assert ids <= watching_it


@requires_postgres
@pytest.mark.asyncio
async def test_unwatch_reports_whether_it_removed_anything(live_db: str) -> None:
    from archguard.db import session_scope
    from archguard.db.store import is_watched, unwatch_repository, watch_repository

    async with session_scope() as s:
        user = await _a_user(s, 91006, "watcher-f")
        await watch_repository(s, user.id, _URL)
        uid = user.id

    async with session_scope() as s:
        assert await unwatch_repository(s, uid, _URL) is True

    async with session_scope() as s:
        assert await is_watched(s, uid, _URL) is False
        # Second removal changed nothing, and must say so rather than pretend.
        assert await unwatch_repository(s, uid, _URL) is False


@requires_postgres
@pytest.mark.asyncio
async def test_unwatching_a_repository_never_seen_is_false_not_an_error(live_db: str) -> None:
    from archguard.db import session_scope
    from archguard.db.store import unwatch_repository

    async with session_scope() as s:
        user = await _a_user(s, 91007, "watcher-g")
        assert await unwatch_repository(s, user.id, "https://github.com/o/never.git") is False


@requires_postgres
@pytest.mark.asyncio
async def test_a_new_watch_has_no_last_seen_sha(live_db: str) -> None:
    """Null until the first check, so a freshly watched repo is always scanned."""
    from archguard.db import session_scope
    from archguard.db.store import list_watched, watch_repository

    async with session_scope() as s:
        user = await _a_user(s, 91008, "watcher-h")
        await watch_repository(s, user.id, _URL)
        uid = user.id

    async with session_scope() as s:
        entry = next(w for w in await list_watched(s, uid) if w["repo_url"] == _URL)
        assert entry["last_seen_sha"] is None
        assert entry["last_checked_at"] is None


@requires_postgres
@pytest.mark.asyncio
async def test_recording_a_check_stores_the_sha(live_db: str) -> None:
    from archguard.db import session_scope
    from archguard.db.store import list_watched, record_watch_check, watch_repository

    async with session_scope() as s:
        user = await _a_user(s, 91009, "watcher-i")
        watch = await watch_repository(s, user.id, _URL)
        uid, rid = user.id, watch.repository_id

    async with session_scope() as s:
        await record_watch_check(s, uid, rid, "a" * 40)

    async with session_scope() as s:
        entry = next(w for w in await list_watched(s, uid) if w["repo_url"] == _URL)
        assert entry["last_seen_sha"] == "a" * 40
        assert entry["last_checked_at"] is not None


@requires_postgres
@pytest.mark.asyncio
async def test_a_check_that_found_nothing_still_records_the_timestamp(live_db: str) -> None:
    """A stuck watcher must be visible.

    With no sha to record -- an unreachable remote, say -- the poll time is
    still written. Otherwise a watcher that has silently stopped looks exactly
    like a repository nobody has changed.
    """
    from archguard.db import session_scope
    from archguard.db.store import list_watched, record_watch_check, watch_repository

    async with session_scope() as s:
        user = await _a_user(s, 91010, "watcher-j")
        watch = await watch_repository(s, user.id, _URL)
        uid, rid = user.id, watch.repository_id

    async with session_scope() as s:
        await record_watch_check(s, uid, rid, None)

    async with session_scope() as s:
        entry = next(w for w in await list_watched(s, uid) if w["repo_url"] == _URL)
        assert entry["last_checked_at"] is not None
        assert entry["last_seen_sha"] is None


@requires_postgres
@pytest.mark.asyncio
async def test_recording_against_an_unwatched_repository_is_a_no_op(live_db: str) -> None:
    """The watch can be removed between the poll and the write."""
    from archguard.db import session_scope
    from archguard.db.store import record_watch_check

    async with session_scope() as s:
        # No such watch row; must not raise.
        await record_watch_check(s, 91011, 987654, "b" * 40)
