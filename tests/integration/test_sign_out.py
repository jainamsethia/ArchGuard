"""Signing out has to end the session, not just the browser's copy of it.

`_sessions.revoke` is unit-tested and correct. What was never tested is the
flow a user actually performs, and the flow had no way to start: `signOut()`
existed in auth.js with no caller anywhere in the product, so the only way to
stop being signed in was to wait 24 hours or clear cookies by hand. The privacy
policy meanwhile told people that signing out ends the session immediately.

The distinction these tests exist for is between deleting a cookie and ending a
session. A sign-out that only clears the browser's copy leaves the session
alive on the server, so anyone who kept the cookie value -- from a shared
machine, a synced profile, a proxy log -- can keep using it. So the cookie is
replayed after signing out, and required to fail.

Against real PostgreSQL and real Redis: the session store is Redis, and a test
that stubbed it would be asserting that the stub forgot something.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.db_fixtures import TEST_SESSION_SECRET, requires_postgres

pytestmark = pytest.mark.integration

#: Endpoints a signed-out caller must not be able to use. One per router, so a
#: sign-out that missed a single mounted router would show up here.
PROTECTED = [
    "/api/v1/jobs",
    "/api/v1/runs",
    "/api/v1/suppressions",
    "/api/v1/watch",
]


@pytest.fixture(autouse=True)
def _no_dev_login(monkeypatch):
    """Exercise the production authentication path.

    With no OAuth app configured and a loopback peer, `dev_login_permitted`
    signs every request in as the local development account -- so a revoked
    session is immediately replaced by a fresh one and sign-out looks broken
    when it is not. That fallback is deliberate and cannot be reached in
    production (the startup check refuses to boot without OAuth), but it is not
    the path these tests are about.

    Configuring an OAuth app is what turns it off: once there is a real way to
    sign in, the fallback stops existing rather than sitting there as a second
    one. The credentials are never used -- nothing here starts an OAuth flow.
    """
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "not-a-real-client-id")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "not-a-real-client-secret")


@pytest.fixture()
def signed_in(live_db, monkeypatch):
    """A client holding a real session for a real account."""
    from fastapi.testclient import TestClient

    from archguard.dashboard import _sessions
    from archguard.dashboard.app import app
    from archguard.db import store
    from archguard.db.session import session_scope

    monkeypatch.setenv("SESSION_SECRET", TEST_SESSION_SECRET)

    async def _user() -> int:
        async with session_scope() as session:
            user = await store.upsert_user(session, github_id=8701, login="signs-out")
            return user.id

    user_id = asyncio.run(_user())
    cookie = _sessions.issue(user_id)

    client = TestClient(app)
    client.cookies.set(_sessions.COOKIE_NAME, cookie)
    return {"client": client, "cookie": cookie, "user_id": user_id}


# ------------------------------------------------------------- the happy path


@requires_postgres
def test_a_signed_in_user_is_signed_in(signed_in):
    """The baseline. Without it, every assertion below could pass because the
    session never worked."""
    body = signed_in["client"].get("/api/v1/auth/status").json()
    assert body["authenticated"] is True
    assert body["user"]["login"] == "signs-out"


@requires_postgres
def test_signing_out_reports_signed_out(signed_in):
    client = signed_in["client"]
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/status").json()["authenticated"] is False


@requires_postgres
def test_the_browser_is_told_to_drop_the_cookie(signed_in):
    """Necessary, and on its own not sufficient -- see the next test.

    Asserted on the response header rather than on the test client's cookie
    jar. The jar holds a cookie set here with no domain, which the deletion
    (scoped to the request's domain, as a real one is) does not match -- an
    artefact of how the fixture signs in, not of what the server sends.
    """
    from archguard.dashboard import _sessions

    response = signed_in["client"].post("/api/v1/auth/logout")
    header = response.headers.get("set-cookie", "")

    assert _sessions.COOKIE_NAME in header, (
        f"sign-out sent no cookie instruction at all: {header!r}"
    )
    assert "Max-Age=0" in header or "max-age=0" in header.lower(), (
        f"the cookie was not expired: {header!r}"
    )
    assert "Path=/" in header, f"a deletion scoped to the wrong path: {header!r}"


# ---------------------------------------------------- the part that matters


@requires_postgres
def test_the_old_cookie_no_longer_works(signed_in):
    """Replay the exact cookie value after signing out.

    This is what separates ending a session from clearing a cookie. Anyone
    holding the value -- a shared machine, a synced browser profile, a proxy
    log -- would otherwise stay signed in for the rest of the 24-hour TTL, and
    nothing the user did could stop them.
    """
    from fastapi.testclient import TestClient

    from archguard.dashboard import _sessions
    from archguard.dashboard.app import app

    signed_in["client"].post("/api/v1/auth/logout")

    # A different client, carrying the cookie the first one had.
    replay = TestClient(app)
    replay.cookies.set(_sessions.COOKIE_NAME, signed_in["cookie"])

    assert replay.get("/api/v1/auth/status").json()["authenticated"] is False, (
        "the session survived sign-out: the cookie was cleared but the server "
        "still honours it"
    )


@requires_postgres
def test_the_session_is_gone_from_the_store(signed_in):
    """Stated against the store directly, so the reason is unambiguous."""
    from archguard.dashboard import _sessions

    assert _sessions.resolve(signed_in["cookie"]) == signed_in["user_id"]
    signed_in["client"].post("/api/v1/auth/logout")
    assert _sessions.resolve(signed_in["cookie"]) is None


@requires_postgres
@pytest.mark.parametrize("path", PROTECTED)
def test_protected_endpoints_refuse_the_old_cookie(signed_in, path):
    """Every mounted router, not just one.

    Sign-out that ended the session for some routes and not others would be
    worse than none, because the user would be told they had signed out.
    """
    from fastapi.testclient import TestClient

    from archguard.dashboard import _sessions
    from archguard.dashboard.app import app

    signed_in["client"].post("/api/v1/auth/logout")

    replay = TestClient(app)
    replay.cookies.set(_sessions.COOKIE_NAME, signed_in["cookie"])

    assert replay.get(path).status_code == 401, (
        f"{path} still served a request carrying a revoked session"
    )


# --------------------------------------------------------------- edge cases


@requires_postgres
def test_signing_out_twice_is_not_an_error(signed_in):
    """A double click, or a retry on a flaky connection."""
    client = signed_in["client"]
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 200


@requires_postgres
def test_signing_out_without_a_session_is_not_an_error(live_db, monkeypatch):
    """Reachable by anyone who bookmarks the call, and by a user whose session
    expired while the page was open."""
    from fastapi.testclient import TestClient

    from archguard.dashboard.app import app

    monkeypatch.setenv("SESSION_SECRET", TEST_SESSION_SECRET)
    assert TestClient(app).post("/api/v1/auth/logout").status_code == 200


@requires_postgres
@pytest.mark.parametrize("bad", ["", "no-dot", "...", "x.y.z"])
def test_signing_out_with_a_malformed_cookie_is_not_an_error(live_db, monkeypatch, bad):
    """Signing out is the thing a user reaches for when something is wrong, so
    it must not be the thing that breaks on a corrupted cookie."""
    from fastapi.testclient import TestClient

    from archguard.dashboard import _sessions
    from archguard.dashboard.app import app

    monkeypatch.setenv("SESSION_SECRET", TEST_SESSION_SECRET)
    client = TestClient(app)
    if bad:
        client.cookies.set(_sessions.COOKIE_NAME, bad)
    assert client.post("/api/v1/auth/logout").status_code == 200


@requires_postgres
def test_signing_out_does_not_end_anybody_elses_session(live_db, monkeypatch):
    """One user's sign-out is not a global event.

    `reset_sessions()` exists for tests and clears every session there is;
    revoke must not behave like it.
    """
    from fastapi.testclient import TestClient

    from archguard.dashboard import _sessions
    from archguard.dashboard.app import app
    from archguard.db import store
    from archguard.db.session import session_scope

    monkeypatch.setenv("SESSION_SECRET", TEST_SESSION_SECRET)

    async def _users() -> tuple[int, int]:
        async with session_scope() as session:
            a = await store.upsert_user(session, github_id=8703, login="stays-in-a")
            b = await store.upsert_user(session, github_id=8704, login="stays-in-b")
            return a.id, b.id

    a_id, b_id = asyncio.run(_users())
    a_cookie, b_cookie = _sessions.issue(a_id), _sessions.issue(b_id)

    a = TestClient(app)
    a.cookies.set(_sessions.COOKIE_NAME, a_cookie)
    a.post("/api/v1/auth/logout")

    b = TestClient(app)
    b.cookies.set(_sessions.COOKIE_NAME, b_cookie)
    assert b.get("/api/v1/auth/status").json()["authenticated"] is True, (
        "signing one account out ended another's session"
    )


@requires_postgres
def test_a_new_sign_in_after_signing_out_gets_a_different_session(signed_in):
    """The replacement session must not be the revoked one."""
    from archguard.dashboard import _sessions

    signed_in["client"].post("/api/v1/auth/logout")
    fresh = _sessions.issue(signed_in["user_id"])

    assert fresh != signed_in["cookie"]
    assert _sessions.resolve(fresh) == signed_in["user_id"]
    assert _sessions.resolve(signed_in["cookie"]) is None


def _paths_of(app: Any) -> set[str]:
    return {getattr(r, "path", "") for r in app.routes}


def test_the_logout_route_is_mounted_where_the_client_calls_it():
    """auth.js posts to /api/v1/auth/logout. A route mounted anywhere else
    would fail silently -- the client ignores the response and redirects
    regardless, so the user would be told they had signed out either way.
    """
    from archguard.dashboard.app import app

    assert "/api/v1/auth/logout" in _paths_of(app)
