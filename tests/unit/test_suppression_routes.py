"""End-to-end tests for the /api/v1/suppressions dashboard routes.

Covers GET/POST/DELETE against PostgreSQL, which is where suppressions live
since the JSONL store was retired. Nothing is faked: the routes, the store and
the ownership checks are all exercised, because the bugs worth catching here are
the ones where those three stop agreeing.
"""
from __future__ import annotations

import pytest

from tests.db_fixtures import requires_postgres

REPO_URL = "https://github.com/example/repo"


@pytest.fixture
def job_id(seed_run) -> str:
    """A real job owned by ``test_user``, so a repository can be resolved."""
    return seed_run(repo_url=REPO_URL, score=50.0, band="WARN", violations=[])


@requires_postgres
def test_suppression_lifecycle(auth_client, job_id: str) -> None:
    """POST then GET then DELETE a suppression end-to-end."""
    q = f"?job_id={job_id}"

    resp = auth_client.post(
        f"/api/v1/suppressions{q}",
        json={"module": "api", "layer": 1, "message": "bad import", "reason": "FP"},
    )
    assert resp.status_code == 200, resp.text

    resp = auth_client.get(f"/api/v1/suppressions{q}")
    assert resp.status_code == 200
    sups = resp.json()["suppressions"]
    assert len(sups) == 1
    assert sups[0]["module"] == "api"
    assert sups[0]["reason"] == "FP"

    resp = auth_client.request(
        "DELETE",
        f"/api/v1/suppressions{q}",
        json={"suppression_id": sups[0]["id"]},
    )
    assert resp.status_code == 200, resp.text

    assert auth_client.get(f"/api/v1/suppressions{q}").json()["suppressions"] == []


@requires_postgres
def test_delete_missing_suppression_returns_404(auth_client, job_id: str) -> None:
    """A no-op delete must not report success."""
    resp = auth_client.request(
        "DELETE",
        f"/api/v1/suppressions?job_id={job_id}",
        json={"suppression_id": "00000000-0000-4000-8000-000000000000"},
    )
    assert resp.status_code == 404


@requires_postgres
def test_the_response_never_carries_another_users_id(auth_client, job_id: str) -> None:
    """The payload is built field by field for this reason.

    While these were dataclasses in a file the route returned ``s.__dict__``.
    On an ORM row that carries ``user_id`` and SQLAlchemy's instance state.
    """
    auth_client.post(
        f"/api/v1/suppressions?job_id={job_id}",
        json={"module": "api", "layer": 1, "message": "bad import", "reason": "FP"},
    )

    sups = auth_client.get(f"/api/v1/suppressions?job_id={job_id}").json()["suppressions"]

    assert sups and "user_id" not in sups[0]
    assert not any(k.startswith("_") for k in sups[0])


@requires_postgres
def test_a_suppression_is_not_visible_to_another_user(auth_client, job_id: str) -> None:
    """The isolation the file store could not provide.

    It was keyed by repository, so everyone analysing the same repository shared
    one file and one another's stated reasons.
    """
    from fastapi.testclient import TestClient

    from archguard.dashboard import _sessions
    from archguard.dashboard.app import app
    from archguard.db.models import User
    from archguard.db.session import session_scope

    auth_client.post(
        f"/api/v1/suppressions?job_id={job_id}",
        json={"module": "api", "layer": 1, "message": "bad import", "reason": "mine"},
    )
    mine = auth_client.get(f"/api/v1/suppressions?job_id={job_id}").json()["suppressions"]
    assert len(mine) == 1

    async def _other_user() -> int:
        async with session_scope() as session:
            other = User(github_id=424242, login="intruder")
            session.add(other)
            await session.flush()
            return int(other.id)

    import asyncio

    other_id = asyncio.run(_other_user())
    intruder = TestClient(app)
    intruder.cookies.set(_sessions.COOKIE_NAME, _sessions.issue(other_id))

    # They cannot see it. The job is not theirs either, so the repository does
    # not resolve and the list is empty rather than somebody else's.
    assert intruder.get(f"/api/v1/suppressions?job_id={job_id}").json()["suppressions"] == []

    # And they cannot delete it by naming its id, which is a bare UUID in a
    # request body: the lookup is scoped, so this is a 404 rather than a delete.
    resp = intruder.request(
        "DELETE",
        f"/api/v1/suppressions?job_id={job_id}",
        json={"suppression_id": mine[0]["id"]},
    )
    assert resp.status_code == 404

    still_there = auth_client.get(
        f"/api/v1/suppressions?job_id={job_id}"
    ).json()["suppressions"]
    assert len(still_there) == 1


@requires_postgres
def test_a_job_that_resolves_no_repository_is_rejected(auth_client) -> None:
    """Silently accepting this is what the old job-scoped fallback file did.

    It wrote somewhere the next scan would never look, and answered "success".
    """
    resp = auth_client.post(
        "/api/v1/suppressions?job_id=00000000-0000-4000-8000-000000000000",
        json={"module": "api", "layer": 1, "message": "bad import", "reason": "FP"},
    )
    assert resp.status_code == 400


@requires_postgres
def test_a_reason_longer_than_the_limit_is_rejected(auth_client, job_id: str) -> None:
    """The JSONL store enforced this in Python; it is a field constraint now."""
    resp = auth_client.post(
        f"/api/v1/suppressions?job_id={job_id}",
        json={
            "module": "api",
            "layer": 1,
            "message": "bad import",
            "reason": "x" * 501,
        },
    )
    assert resp.status_code == 422
    assert "reason" in resp.text


@requires_postgres
def test_a_reason_containing_a_newline_is_rejected(auth_client, job_id: str) -> None:
    """A newline lets one reason impersonate further entries when exported."""
    resp = auth_client.post(
        f"/api/v1/suppressions?job_id={job_id}",
        json={
            "module": "api",
            "layer": 1,
            "message": "bad import",
            "reason": "looks fine\nSUPPRESSION_CREATED: something else",
        },
    )
    assert resp.status_code == 422


@requires_postgres
@pytest.mark.parametrize("layer", [0, 5, -1])
def test_a_layer_that_cannot_produce_a_finding_is_rejected(
    auth_client, job_id: str, layer: int
) -> None:
    """Otherwise the row sits there forever, matching nothing."""
    resp = auth_client.post(
        f"/api/v1/suppressions?job_id={job_id}",
        json={
            "module": "api",
            "layer": layer,
            "message": "bad import",
            "reason": "FP",
        },
    )
    assert resp.status_code == 422


@requires_postgres
def test_creating_a_suppression_is_audited(auth_client, job_id: str, monkeypatch) -> None:
    """Deciding to stop reporting a finding belongs in the audit trail.

    The JSONL store logged this and the first cut of the PostgreSQL route did
    not, which is a capability lost in a refactor rather than a decision.
    """
    from archguard.audit import logger as audit_logger

    logged: list[tuple] = []

    class _Spy:
        def log(self, event, **fields):
            logged.append((event, fields))

    monkeypatch.setattr(audit_logger, "AuditLogger", lambda *a, **k: _Spy())

    resp = auth_client.post(
        f"/api/v1/suppressions?job_id={job_id}",
        json={"module": "api", "layer": 1, "message": "bad import", "reason": "FP"},
    )
    assert resp.status_code == 200

    assert [e for e, _ in logged] == ["SUPPRESSION_CREATED"]
    assert logged[0][1]["module"] == "api"
    assert logged[0][1]["layer"] == 1


@requires_postgres
def test_the_list_names_the_account_that_filed_it(auth_client, job_id: str) -> None:
    """``created_by`` was "local" when there were no accounts. There are now."""
    auth_client.post(
        f"/api/v1/suppressions?job_id={job_id}",
        json={"module": "api", "layer": 1, "message": "bad import", "reason": "FP"},
    )

    sups = auth_client.get(f"/api/v1/suppressions?job_id={job_id}").json()["suppressions"]
    assert sups[0]["created_by"] == "test-user"
