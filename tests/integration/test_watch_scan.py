"""The scheduled pass over watched repositories (P3-1).

The pass exists to make watching affordable. ADR-009 measured re-analysing an
unchanged repository at ~4s in a warm worker and concluded that the gate worth
having is the commit, not the file: ``git ls-remote`` answers "has anything
changed" for a few hundred bytes and no clone. These tests pin that gate --
unchanged means no analysis at all -- and the failure behaviour around it,
because a scheduler that dies quietly is worse than no scheduler.

``remote_head`` and ``enqueue_analysis`` are substituted, so the pass is
exercised without reaching GitHub or a queue. Everything between them is the
real code path, including the database writes.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.db_fixtures import requires_postgres

_URL = "https://github.com/pallets/flask.git"
_OTHER = "https://github.com/psf/requests.git"
_SHA_A = "a" * 40
_SHA_B = "b" * 40


async def _watch(url: str = _URL, github_id: int = 92001, login: str = "sched") -> tuple[int, int]:
    """Create a user watching *url*. Returns (user_id, repository_id)."""
    from archguard.db.session import session_scope
    from archguard.db.store import upsert_user, watch_repository

    async with session_scope() as s:
        user = await upsert_user(s, github_id=github_id, login=login)
        watch = await watch_repository(s, user.id, url)
        return user.id, watch.repository_id


async def _entry_for(user_id: int) -> dict[str, Any]:
    from archguard.db.session import session_scope
    from archguard.db.store import list_watched

    async with session_scope() as s:
        return (await list_watched(s, user_id))[0]


@pytest.fixture()
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Record what the pass would have enqueued, without a queue."""
    seen: dict[str, list[Any]] = {"enqueued": [], "polled": []}

    async def _fake_enqueue(job_id: str) -> str:
        seen["enqueued"].append(job_id)
        return "queued"

    monkeypatch.setattr("archguard.worker.queue.enqueue_analysis", _fake_enqueue)
    return seen


def _head_returns(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, str | None]) -> None:
    async def _fake_head(clone_url: str) -> str | None:
        return mapping.get(clone_url)

    monkeypatch.setattr("archguard.dashboard.workspace.remote_head", _fake_head)


@requires_postgres
@pytest.mark.asyncio
async def test_a_repository_that_has_not_moved_is_not_analysed(
    live_db: str, captured: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the pass. An unchanged repository costs one round trip."""
    from archguard.db.session import session_scope
    from archguard.db.store import record_watch_check
    from archguard.worker.watch import scan_watched_repositories

    user_id, repo_id = await _watch()
    async with session_scope() as s:
        await record_watch_check(s, user_id, repo_id, _SHA_A)

    _head_returns(monkeypatch, {_URL: _SHA_A})
    assert await scan_watched_repositories() == 0
    assert captured["enqueued"] == []

    # The poll still happened, and is recorded.
    assert (await _entry_for(user_id))["last_checked_at"] is not None


@requires_postgres
@pytest.mark.asyncio
async def test_a_moved_head_enqueues_an_analysis_and_records_the_new_sha(
    live_db: str, captured: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from archguard.db.session import session_scope
    from archguard.db.store import record_watch_check
    from archguard.worker.watch import scan_watched_repositories

    user_id, repo_id = await _watch()
    async with session_scope() as s:
        await record_watch_check(s, user_id, repo_id, _SHA_A)

    _head_returns(monkeypatch, {_URL: _SHA_B})
    assert await scan_watched_repositories() == 1
    assert len(captured["enqueued"]) == 1

    assert (await _entry_for(user_id))["last_seen_sha"] == _SHA_B


@requires_postgres
@pytest.mark.asyncio
async def test_a_freshly_watched_repository_is_always_scanned_once(
    live_db: str, captured: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """last_seen_sha is null until the first check, so the first pass analyses."""
    from archguard.worker.watch import scan_watched_repositories

    user_id, _ = await _watch()
    _head_returns(monkeypatch, {_URL: _SHA_A})

    assert await scan_watched_repositories() == 1
    assert (await _entry_for(user_id))["last_seen_sha"] == _SHA_A


@requires_postgres
@pytest.mark.asyncio
async def test_an_unreachable_remote_records_the_check_without_enqueueing(
    live_db: str, captured: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleted, renamed or made private. Not a reason to clone anything.

    The timestamp is still written: a watcher that has stopped working must not
    look identical to a repository nobody has touched.
    """
    from archguard.db.session import session_scope
    from archguard.db.store import record_watch_check
    from archguard.worker.watch import scan_watched_repositories

    user_id, repo_id = await _watch()
    async with session_scope() as s:
        await record_watch_check(s, user_id, repo_id, _SHA_A)

    _head_returns(monkeypatch, {_URL: None})
    assert await scan_watched_repositories() == 0
    assert captured["enqueued"] == []

    entry = await _entry_for(user_id)
    assert entry["last_checked_at"] is not None
    # The known-good sha is not clobbered by a failed poll.
    assert entry["last_seen_sha"] == _SHA_A


@requires_postgres
@pytest.mark.asyncio
async def test_one_unreachable_repository_does_not_stop_the_others(
    live_db: str, captured: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from archguard.worker.watch import scan_watched_repositories

    await _watch(_URL, github_id=92010, login="sched-a")
    await _watch(_OTHER, github_id=92011, login="sched-b")

    _head_returns(monkeypatch, {_URL: None, _OTHER: _SHA_B})
    assert await scan_watched_repositories() == 1
    assert len(captured["enqueued"]) == 1


@requires_postgres
@pytest.mark.asyncio
async def test_the_pass_can_be_switched_off(
    live_db: str, captured: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from archguard.worker.watch import scan_watched_repositories

    await _watch()
    _head_returns(monkeypatch, {_URL: _SHA_B})
    monkeypatch.setenv("ARCHGUARD_WATCH_ENABLED", "0")

    assert await scan_watched_repositories() == 0
    assert captured["enqueued"] == []


@requires_postgres
@pytest.mark.asyncio
async def test_the_per_pass_cap_defers_rather_than_drops(
    live_db: str, captured: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Over the cap, a repository must be picked up next pass, not skipped.

    That only works if its sha is left unrecorded -- recording it would make the
    next pass conclude the repository had already been handled, and the change
    would never be analysed at all.
    """
    import archguard.worker.watch as watch_mod
    from archguard.worker.watch import scan_watched_repositories

    user_a, _ = await _watch(_URL, github_id=92020, login="cap-a")
    user_b, _ = await _watch(_OTHER, github_id=92021, login="cap-b")

    monkeypatch.setattr(watch_mod, "MAX_ENQUEUED_PER_PASS", 1)
    _head_returns(monkeypatch, {_URL: _SHA_A, _OTHER: _SHA_B})

    assert await scan_watched_repositories() == 1
    assert len(captured["enqueued"]) == 1

    # all_watched orders by (user_id, repository_id), so the first watch is the
    # one that fits under the cap and the second is the one deferred.
    first, second = await _entry_for(user_a), await _entry_for(user_b)
    assert first["last_seen_sha"] == _SHA_A, "the enqueued repository should record its sha"
    assert second["last_seen_sha"] is None, (
        "the deferred repository recorded its sha, so the next pass will treat it "
        "as already handled and the change is never analysed"
    )

    # Raising the cap on the next pass picks up exactly the deferred one.
    monkeypatch.setattr(watch_mod, "MAX_ENQUEUED_PER_PASS", 20)
    assert await scan_watched_repositories() == 1
    assert (await _entry_for(user_b))["last_seen_sha"] == _SHA_B


@requires_postgres
@pytest.mark.asyncio
async def test_an_empty_watch_list_does_no_work(
    live_db: str, captured: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from archguard.worker.watch import scan_watched_repositories

    polled: list[str] = []

    async def _fake_head(clone_url: str) -> str | None:
        polled.append(clone_url)
        return _SHA_A

    monkeypatch.setattr("archguard.dashboard.workspace.remote_head", _fake_head)

    assert await scan_watched_repositories() == 0
    assert polled == []
