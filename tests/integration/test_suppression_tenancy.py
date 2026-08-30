"""Suppressions belong to the person who made them.

They were stored in a file named after the repository, so two accounts that
had each analysed the same public repository shared one store. Either could
read the other's suppressions -- including the free-text reason, which is
whatever someone typed and can name a deadline, a team or an internal ticket
-- and either could delete them. Nothing about that required an attack: it was
the ordinary result of two people analysing the same popular repository.

`user.id` was present at every call site, but it was spent resolving the job to
a repository URL and then dropped, so the identity that reached storage was the
repository alone.

These tests are at the HTTP layer rather than against the store, deliberately.
The store can be perfectly scoped and the routes still hand it the wrong
identity, which is exactly what happened, and a test that called the store
directly would have agreed that everything was fine.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.db_fixtures import TEST_SESSION_SECRET, requires_postgres

pytestmark = pytest.mark.integration

#: One public repository, analysed by both accounts. That is the whole setup:
#: no shared job, no shared link, nothing either user did wrong.
SHARED_REPO = "https://github.com/public/shared-project"


def _client_for(user_id: int) -> Any:
    from fastapi.testclient import TestClient

    from archguard.dashboard import _sessions
    from archguard.dashboard.app import app

    client = TestClient(app)
    client.cookies.set(_sessions.COOKIE_NAME, _sessions.issue(user_id))
    return client


@pytest.fixture()
def two_accounts(live_db, monkeypatch):
    """Alice and Bob, each with a completed run of the same public repository."""
    import asyncio

    monkeypatch.setenv("SESSION_SECRET", TEST_SESSION_SECRET)

    from archguard.db import store
    from archguard.db.session import session_scope

    async def _seed() -> dict[str, Any]:
        out: dict[str, Any] = {}
        async with session_scope() as session:
            for name, github_id in (("alice", 8801), ("bob", 8802)):
                user = await store.upsert_user(
                    session, github_id=github_id, login=f"supp-{name}"
                )
                job = await store.create_job(session, SHARED_REPO, user_id=user.id)
                await store.persist_run(
                    session,
                    job.id,
                    {"repo_url": SHARED_REPO, "score": 50.0, "violations": []},
                )
                out[name] = {"user_id": user.id, "job_id": job.id}
        return out

    seeded = asyncio.run(_seed())
    return {
        "alice": {**seeded["alice"], "client": _client_for(seeded["alice"]["user_id"])},
        "bob": {**seeded["bob"], "client": _client_for(seeded["bob"]["user_id"])},
    }


def _add(account: dict[str, Any], reason: str, module: str = "core") -> Any:
    return account["client"].post(
        f"/api/v1/suppressions?job_id={account['job_id']}",
        json={
            "module": module,
            "layer": 2,
            "message": "fan_out=9 exceeds budget=3",
            "reason": reason,
        },
    )


def _list(account: dict[str, Any]) -> Any:
    return account["client"].get(
        f"/api/v1/suppressions?job_id={account['job_id']}"
    )


# ------------------------------------------------------------------- reading


@requires_postgres
def test_one_user_cannot_read_anothers_suppressions(two_accounts):
    """The headline. Same repository, two accounts, no sharing of any kind."""
    alice, bob = two_accounts["alice"], two_accounts["bob"]

    assert _add(alice, "ignoring until the Q3 platform migration").status_code == 200

    body = _list(bob).json()
    reasons = [s.get("reason") for s in body.get("suppressions") or []]
    assert reasons == [], (
        f"another account's suppressions were returned: {reasons}"
    )


@requires_postgres
def test_a_user_still_sees_their_own(two_accounts):
    """The other half: scoping must not simply hide everything."""
    alice = two_accounts["alice"]
    assert _add(alice, "known, tracked in ARCH-411").status_code == 200

    body = _list(alice).json()
    reasons = [s.get("reason") for s in body.get("suppressions") or []]
    assert reasons == ["known, tracked in ARCH-411"]


@requires_postgres
def test_two_users_suppress_the_same_finding_independently(two_accounts):
    """Identical module, layer and message from both accounts.

    They hash to the same violation identity, which is precisely why one shared
    store collapsed them into a single record.
    """
    alice, bob = two_accounts["alice"], two_accounts["bob"]
    assert _add(alice, "alice's reason").status_code == 200
    assert _add(bob, "bob's reason").status_code == 200

    assert [s["reason"] for s in _list(alice).json()["suppressions"]] == ["alice's reason"]
    assert [s["reason"] for s in _list(bob).json()["suppressions"]] == ["bob's reason"]


# ------------------------------------------------------------------- deleting


@requires_postgres
def test_one_user_cannot_delete_anothers_suppression(two_accounts):
    """404 rather than 403: telling a stranger that an id exists but is not
    theirs is a slower way of leaking the same fact."""
    alice, bob = two_accounts["alice"], two_accounts["bob"]
    assert _add(alice, "mine").status_code == 200

    created = _list(alice).json()["suppressions"]
    assert created, "alice's suppression was not stored"
    target = created[0]["id"]

    refused = bob["client"].request(
        "DELETE",
        f"/api/v1/suppressions?job_id={bob['job_id']}",
        json={"suppression_id": target},
    )
    assert refused.status_code == 404, (
        f"another account deleted a suppression it does not own "
        f"(status {refused.status_code})"
    )

    # And it is still there.
    assert [s["id"] for s in _list(alice).json()["suppressions"]] == [target]


@requires_postgres
def test_a_user_can_delete_their_own(two_accounts):
    alice = two_accounts["alice"]
    assert _add(alice, "mine to remove").status_code == 200
    target = _list(alice).json()["suppressions"][0]["id"]

    removed = alice["client"].request(
        "DELETE",
        f"/api/v1/suppressions?job_id={alice['job_id']}",
        json={"suppression_id": target},
    )
    assert removed.status_code == 200
    assert _list(alice).json()["suppressions"] == []


# ------------------------------------------------------------------ filtering


@requires_postgres
def test_one_users_suppression_does_not_hide_anothers_violation(two_accounts):
    """The consequence that reaches the report rather than the API.

    A suppression removes a finding from an analysis. Shared, it removed it
    from *everyone's* analysis of that repository -- so one account could make
    a violation disappear from a stranger's report.
    """
    import asyncio

    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.suppression.models import make_violation_hash

    alice, bob = two_accounts["alice"], two_accounts["bob"]
    assert _add(alice, "hidden by alice").status_code == 200

    target = make_violation_hash("core", 2, "fan_out=9 exceeds budget=3")

    async def _bobs_view() -> list[str]:
        async with session_scope() as session:
            rows = await store.active_suppression_hashes(
                session, bob["user_id"], SHARED_REPO
            )
        return list(rows)

    assert target not in asyncio.run(_bobs_view()), (
        "another account's suppression would hide this violation from Bob's report"
    )


@requires_postgres
def test_an_expired_suppression_stops_hiding_anything(two_accounts):
    """Expiry is per record, and it has to be honoured by the filter as well as
    by the list -- a suppression that lapses but keeps hiding its violation is
    the failure the expiry date exists to prevent."""
    import asyncio
    from datetime import UTC, datetime, timedelta

    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.suppression.models import make_violation_hash

    alice = two_accounts["alice"]

    async def _seed_expired() -> None:
        async with session_scope() as session:
            await store.add_suppression(
                session,
                user_id=alice["user_id"],
                repo_url=SHARED_REPO,
                module="core",
                layer=2,
                violation_hash=make_violation_hash("core", 2, "old finding"),
                reason="lapsed",
                expires_at=datetime.now(UTC) - timedelta(days=1),
            )

    async def _active() -> list[str]:
        async with session_scope() as session:
            return list(
                await store.active_suppression_hashes(
                    session, alice["user_id"], SHARED_REPO
                )
            )

    asyncio.run(_seed_expired())
    assert make_violation_hash("core", 2, "old finding") not in asyncio.run(_active()), (
        "an expired suppression is still hiding its violation"
    )


# ------------------------------------------------------- the rest of the API
#
# These moved here from tests/unit/test_suppression_routes.py, which drove the
# routes with a bearer token and a temp directory standing in for a workspace.
# That was a faithful test of the file store; against PostgreSQL it tested a
# code path the product no longer has.


@requires_postgres
def test_deleting_an_id_that_never_existed_is_a_404(two_accounts):
    """Not a silent 200. A delete that reports success without deleting
    anything is how a user believes a finding is coming back and it is not."""
    alice = two_accounts["alice"]
    resp = alice["client"].request(
        "DELETE",
        f"/api/v1/suppressions?job_id={alice['job_id']}",
        json={"suppression_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert resp.status_code == 404


@requires_postgres
def test_a_layer_outside_one_to_four_is_rejected(two_accounts):
    """There are four layers. A suppression against layer 9 would be stored
    under an identity nothing can ever produce, and so never match."""
    alice = two_accounts["alice"]
    resp = alice["client"].post(
        f"/api/v1/suppressions?job_id={alice['job_id']}",
        json={"module": "api", "layer": 9, "message": "msg", "reason": "r"},
    )
    assert resp.status_code in (400, 422), (
        f"an impossible layer was accepted (status {resp.status_code})"
    )


@requires_postgres
def test_suppressing_against_someone_elses_job_is_a_404(two_accounts):
    """The job id is the only thing the caller supplies that names a
    repository, so it is the obvious thing to borrow."""
    alice, bob = two_accounts["alice"], two_accounts["bob"]
    resp = bob["client"].post(
        f"/api/v1/suppressions?job_id={alice['job_id']}",
        json={
            "module": "core",
            "layer": 2,
            "message": "fan_out=9 exceeds budget=3",
            "reason": "borrowed",
        },
    )
    assert resp.status_code == 404, (
        f"a suppression was created against another account's job "
        f"(status {resp.status_code})"
    )
