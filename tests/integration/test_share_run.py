"""Shareable run links: the persistence layer (P3-2).

A share token is the entire credential for reading one analysis without an
account, so these tests are mostly about what it must *not* unlock. The
sharpest edge is that ``share_token`` is nullable: a lookup that let an empty
or missing token through would match every unshared run in the table, turning
"share one report" into "publish all of them".
"""

from __future__ import annotations

import pytest

from tests.db_fixtures import requires_postgres

_URL = "https://github.com/pallets/flask.git"


async def _a_run(github_id: int, login: str, score: float = 87.5) -> tuple[int, str]:
    """A user with one completed run. Returns (user_id, job_id)."""
    from archguard.db.session import session_scope
    from archguard.db.store import create_job, persist_run, upsert_user

    async with session_scope() as s:
        user = await upsert_user(s, github_id=github_id, login=login)
        user_id = user.id

    async with session_scope() as s:
        job = await create_job(s, _URL, user_id=user_id)
        job_id = job.id
        await persist_run(
            s,
            job_id=job_id,
            payload={
                "score": score,
                "band": "PASS",
                "module_scores": {"api": 90.0},
                "violations": [],
                "layer_results": [],
                "metrics": {},
            },
            commit_sha="c" * 40,
        )
    return user_id, job_id


@requires_postgres
@pytest.mark.asyncio
async def test_a_run_is_not_shared_until_someone_shares_it(live_db: str) -> None:
    from archguard.db.session import session_scope
    from archguard.db.store import get_shared_run

    await _a_run(95001, "share-a")
    async with session_scope() as s:
        # The only state a run reaches on its own.
        assert await get_shared_run(s, "") is None
        assert await get_shared_run(s, "x" * 40) is None


@requires_postgres
@pytest.mark.asyncio
async def test_an_empty_token_does_not_unlock_every_unshared_run(live_db: str) -> None:
    """share_token is nullable, so a query for NULL would match all of them.

    This is the failure that turns one shared report into a full disclosure of
    every analysis in the table, and it is one missing guard away at all times.
    """
    from archguard.db.session import session_scope
    from archguard.db.store import get_shared_run

    await _a_run(95002, "share-b")
    await _a_run(95003, "share-c")

    async with session_scope() as s:
        for empty in ("", " ", None):
            assert await get_shared_run(s, empty) is None  # type: ignore[arg-type]


@requires_postgres
@pytest.mark.asyncio
async def test_sharing_returns_a_token_that_unlocks_exactly_that_run(live_db: str) -> None:
    from archguard.db.session import session_scope
    from archguard.db.store import get_shared_run, share_run

    user_id, job_id = await _a_run(95004, "share-d", score=64.0)

    async with session_scope() as s:
        token = await share_run(s, job_id, user_id)
    assert token and len(token) >= 32, "a short token is guessable"

    async with session_scope() as s:
        shared = await get_shared_run(s, token)
    assert shared is not None
    assert shared["job_id"] == job_id
    assert shared["score"] == 64.0


@requires_postgres
@pytest.mark.asyncio
async def test_a_partial_token_unlocks_nothing(live_db: str) -> None:
    """Exact match on a unique column, never a prefix."""
    from archguard.db.session import session_scope
    from archguard.db.store import get_shared_run, share_run

    user_id, job_id = await _a_run(95005, "share-e")
    async with session_scope() as s:
        token = await share_run(s, job_id, user_id)

    async with session_scope() as s:
        assert await get_shared_run(s, token[:-1]) is None
        assert await get_shared_run(s, token[:20]) is None
        assert await get_shared_run(s, token + "x") is None


@requires_postgres
@pytest.mark.asyncio
async def test_sharing_is_idempotent(live_db: str) -> None:
    """Clicking twice must not invalidate a link that has already been sent."""
    from archguard.db.session import session_scope
    from archguard.db.store import share_run

    user_id, job_id = await _a_run(95006, "share-f")

    async with session_scope() as s:
        first = await share_run(s, job_id, user_id)
    async with session_scope() as s:
        second = await share_run(s, job_id, user_id)

    assert first == second


@requires_postgres
@pytest.mark.asyncio
async def test_you_cannot_share_someone_elses_run(live_db: str) -> None:
    from archguard.db.session import session_scope
    from archguard.db.store import share_run, upsert_user

    _owner, job_id = await _a_run(95007, "share-owner")

    async with session_scope() as s:
        stranger = await upsert_user(s, github_id=95008, login="share-stranger")
        stranger_id = stranger.id

    async with session_scope() as s:
        # None, not an exception and not a token: the route turns this into the
        # same 404 an unknown job gets, so the endpoint cannot be used to learn
        # which job ids exist.
        assert await share_run(s, job_id, stranger_id) is None


@requires_postgres
@pytest.mark.asyncio
async def test_revoking_makes_the_link_stop_working(live_db: str) -> None:
    from archguard.db.session import session_scope
    from archguard.db.store import get_shared_run, share_run, unshare_run

    user_id, job_id = await _a_run(95009, "share-g")

    async with session_scope() as s:
        token = await share_run(s, job_id, user_id)
    async with session_scope() as s:
        assert await get_shared_run(s, token) is not None

    async with session_scope() as s:
        assert await unshare_run(s, job_id, user_id) is True
    async with session_scope() as s:
        assert await get_shared_run(s, token) is None, "a revoked link still resolves"

    # And the run itself survives revocation.
    async with session_scope() as s:
        from archguard.db.store import get_latest_run

        assert await get_latest_run(s, job_id, user_id) is not None


@requires_postgres
@pytest.mark.asyncio
async def test_revoking_twice_reports_that_it_changed_nothing(live_db: str) -> None:
    from archguard.db.session import session_scope
    from archguard.db.store import share_run, unshare_run

    user_id, job_id = await _a_run(95010, "share-h")
    async with session_scope() as s:
        await share_run(s, job_id, user_id)
    async with session_scope() as s:
        assert await unshare_run(s, job_id, user_id) is True
    async with session_scope() as s:
        assert await unshare_run(s, job_id, user_id) is False


@requires_postgres
@pytest.mark.asyncio
async def test_you_cannot_revoke_someone_elses_share(live_db: str) -> None:
    from archguard.db.session import session_scope
    from archguard.db.store import get_shared_run, share_run, unshare_run, upsert_user

    user_id, job_id = await _a_run(95011, "share-i")
    async with session_scope() as s:
        token = await share_run(s, job_id, user_id)

    async with session_scope() as s:
        stranger = await upsert_user(s, github_id=95012, login="share-stranger-2")
        stranger_id = stranger.id

    async with session_scope() as s:
        assert await unshare_run(s, job_id, stranger_id) is False
    async with session_scope() as s:
        assert await get_shared_run(s, token) is not None, "a stranger revoked the link"


@requires_postgres
@pytest.mark.asyncio
async def test_a_malformed_job_id_is_rejected_rather_than_erroring(live_db: str) -> None:
    """Job ids come from the address bar; a non-uuid must be a miss, not a 500."""
    from archguard.db.session import session_scope
    from archguard.db.store import share_run, unshare_run

    async with session_scope() as s:
        assert await share_run(s, "../../etc/passwd", 1) is None
        assert await unshare_run(s, "not-a-uuid", 1) is False


@requires_postgres
@pytest.mark.asyncio
async def test_two_shared_runs_get_different_tokens(live_db: str) -> None:
    from archguard.db.session import session_scope
    from archguard.db.store import share_run

    user_a, job_a = await _a_run(95013, "share-j")
    user_b, job_b = await _a_run(95014, "share-k")

    async with session_scope() as s:
        token_a = await share_run(s, job_a, user_a)
    async with session_scope() as s:
        token_b = await share_run(s, job_b, user_b)

    assert token_a != token_b
