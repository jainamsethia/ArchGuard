"""The /api/v1/watched routes.

A watch is what makes a repository eligible for the scheduled re-scan, so these
routes decide whose repositories the worker will go and clone on a timer. Two
things therefore matter more than the CRUD: the URL is canonicalised to exactly
the form job submission stores, and one user's watches are invisible to another.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.db_fixtures import requires_postgres

_CANONICAL = "https://github.com/pallets/flask.git"


@pytest.fixture()
def two_clients(live_db: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    """Two signed-in accounts, each with its own client.

    Local rather than shared: tests/unit/test_tenancy.py has a `two_users`
    fixture but it is private to that module and seeds runs these tests do not
    want.
    """
    from fastapi.testclient import TestClient

    from archguard.dashboard import _sessions
    from archguard.dashboard.app import app
    from archguard.db import store
    from archguard.db.session import session_scope
    from tests.db_fixtures import TEST_SESSION_SECRET, _run

    monkeypatch.setenv("SESSION_SECRET", TEST_SESSION_SECRET)
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)

    async def _make(github_id: int, login: str) -> int:
        async with session_scope() as session:
            user = await store.upsert_user(session, github_id=github_id, login=login)
            return int(user.id)

    clients = []
    for github_id, login in ((94001, "watch-one"), (94002, "watch-two")):
        user_id = _run(_make(github_id, login))
        client = TestClient(app)
        client.cookies.set(_sessions.COOKIE_NAME, _sessions.issue(user_id))
        clients.append(client)
    return clients[0], clients[1]


@requires_postgres
def test_watching_stores_the_same_url_job_submission_does(auth_client: Any) -> None:
    """Repositories are keyed by URL.

    A watch stored under any other spelling -- no .git, a trailing slash, the
    browser URL rather than the clone URL -- points at a different repositories
    row than the runs it is meant to be watching, so its trend is always empty
    and it never alerts. Silently.
    """
    for spelling in (
        "https://github.com/pallets/flask",
        "https://github.com/pallets/flask/",
        "https://github.com/pallets/flask.git",
        "git@github.com:pallets/flask.git",
    ):
        resp = auth_client.post("/api/v1/watched", json={"repo_url": spelling})
        assert resp.status_code == 200, resp.text
        assert resp.json()["repo_url"] == _CANONICAL, f"{spelling} was not canonicalised"

    listed = auth_client.get("/api/v1/watched").json()["watched"]
    assert [w["repo_url"] for w in listed] == [_CANONICAL], "four spellings became four watches"


@requires_postgres
def test_a_malformed_url_is_rejected(auth_client: Any) -> None:
    for bad in ("not-a-url", "https://example.com/owner/repo", "https://github.com/../../etc"):
        resp = auth_client.post("/api/v1/watched", json={"repo_url": bad})
        assert resp.status_code == 422, f"{bad!r} was accepted: {resp.text}"


@requires_postgres
def test_watching_is_idempotent_over_the_api(auth_client: Any) -> None:
    for _ in range(3):
        assert auth_client.post("/api/v1/watched", json={"repo_url": _CANONICAL}).status_code == 200
    assert len(auth_client.get("/api/v1/watched").json()["watched"]) == 1


@requires_postgres
def test_unwatching_reports_whether_anything_was_removed(auth_client: Any) -> None:
    auth_client.post("/api/v1/watched", json={"repo_url": _CANONICAL})

    first = auth_client.request("DELETE", "/api/v1/watched", json={"repo_url": _CANONICAL})
    assert first.status_code == 200
    assert first.json()["removed"] is True

    # Removing what is already gone is success -- the caller is a toggle and
    # the state it asked for now holds -- but it says it changed nothing.
    second = auth_client.request("DELETE", "/api/v1/watched", json={"repo_url": _CANONICAL})
    assert second.status_code == 200
    assert second.json()["removed"] is False

    assert auth_client.get("/api/v1/watched").json()["watched"] == []


@requires_postgres
def test_the_list_starts_empty(auth_client: Any) -> None:
    assert auth_client.get("/api/v1/watched").json()["watched"] == []


@requires_postgres
def test_watching_requires_a_signed_in_user(
    live_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anonymous callers must not be able to enrol a repository for cloning.

    OAuth is configured here on purpose. Without it, a loopback request falls
    back to the local development account by design (see
    ``_identity.dev_login_permitted``: not production, no OAuth app, loopback
    peer) -- so asserting 401 on a bare dev instance would be asserting the
    fallback does not exist. Configuring an OAuth app is exactly the condition
    that turns it off, and production cannot start without one.
    """
    from fastapi.testclient import TestClient

    from archguard.dashboard.app import app

    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "test-client-secret")

    anon = TestClient(app)
    resp = anon.post("/api/v1/watched", json={"repo_url": _CANONICAL})
    assert resp.status_code == 401, resp.text


@requires_postgres
def test_one_users_watches_are_invisible_to_another(two_clients: tuple[Any, Any]) -> None:
    """Per-user isolation, the same guarantee every other data route carries."""
    first, second = two_clients

    assert first.post("/api/v1/watched", json={"repo_url": _CANONICAL}).status_code == 200

    assert [w["repo_url"] for w in first.get("/api/v1/watched").json()["watched"]] == [_CANONICAL]
    assert second.get("/api/v1/watched").json()["watched"] == []

    # Nor can the second user remove the first user's watch.
    removed = second.request("DELETE", "/api/v1/watched", json={"repo_url": _CANONICAL})
    assert removed.json()["removed"] is False
    assert len(first.get("/api/v1/watched").json()["watched"]) == 1
