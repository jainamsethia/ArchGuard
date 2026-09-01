"""A signed-in user must be able to use the API in production.

`check_token` accepts three credentials -- an operator Bearer token, a session
cookie, and a short-lived stream token -- but it only looked for any of them
when `ARCHGUARD_DASHBOARD_TOKEN` was set. With no operator token configured it
skipped straight to deciding by peer address.

On a developer's machine that is invisible: the peer is loopback, so everything
is allowed. Behind Railway or Render the peer is the proxy, so nothing is --
and a correctly signed-in user, on an instance that passed every startup check,
got 401 from every `/api/v1` route. The account existed, the session was valid,
and nothing in the request was wrong.

The fix is that a session authenticates a request on its own merits. Whether an
operator has configured a separate credential for their own access is not a
fact about whether this user is signed in.

These tests run against real PostgreSQL and real Redis because a session is a
Redis-backed record of a real row; a stubbed one would be asserting that the
stub remembers.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.db_fixtures import TEST_SESSION_SECRET, requires_postgres

pytestmark = pytest.mark.integration

#: One route per router that a signed-in user reaches through `check_token`.
PROTECTED = [
    "/api/v1/jobs",
    "/api/v1/runs",
    "/api/v1/suppressions",
    "/api/v1/watch",
]

#: Not loopback and not a trusted host, which is what a request forwarded by a
#: platform proxy looks like to the application. `_ALWAYS_TRUSTED_HOSTS` covers
#: TestClient's default peer, so a test that did not set this would be exercising
#: the localhost path while claiming to be production.
REMOTE_PEER = ("203.0.113.9", 5555)


@pytest.fixture()
def production(monkeypatch):
    """Production, configured the way a real deployment is.

    OAuth credentials are present because a production instance has them and
    because `dev_login_permitted` is gated on their absence -- without them the
    loopback fallback would sign every request in and there would be nothing to
    test. They are never used: nothing here starts an OAuth flow.
    """
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SESSION_SECRET", TEST_SESSION_SECRET)
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "not-a-real-client-id")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "not-a-real-client-secret")
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", raising=False)
    return monkeypatch


def _signed_in_user(github_id: int, login: str) -> int:
    from archguard.db import store
    from archguard.db.session import session_scope

    async def _go() -> int:
        async with session_scope() as session:
            user = await store.upsert_user(session, github_id=github_id, login=login)
            return user.id

    return asyncio.run(_go())


def _remote_client(cookie: str | None = None):
    """A client that reaches the app the way a proxied request does."""
    from fastapi.testclient import TestClient

    from archguard.dashboard import _sessions
    from archguard.dashboard.app import app

    client = TestClient(app, client=REMOTE_PEER)
    if cookie:
        client.cookies.set(_sessions.COOKIE_NAME, cookie)
    return client


@pytest.fixture()
def session_cookie(live_db, production) -> str:
    from archguard.dashboard import _sessions

    return _sessions.issue(_signed_in_user(8901, "prod-user"))


# ------------------------------------------------- the reported failure


@requires_postgres
@pytest.mark.parametrize("path", PROTECTED)
def test_a_signed_in_user_works_in_production_without_an_operator_token(
    session_cookie, production, path
):
    """The defect, on every mounted router.

    No `ARCHGUARD_DASHBOARD_TOKEN`, a real session, and a peer address that is
    not loopback -- which is every request on a hosted deployment. This
    returned 401 from all of them.
    """
    production.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)

    response = _remote_client(session_cookie).get(path)

    assert response.status_code != 401, (
        f"{path} refused a signed-in user because no operator token is "
        f"configured: {response.text[:200]}"
    )
    assert response.status_code == 200, response.text


@requires_postgres
def test_the_same_request_works_when_an_operator_token_is_configured(
    session_cookie, production
):
    """The other half: configuring a token must not be what makes sessions work,
    and must not stop them working either."""
    production.setenv("ARCHGUARD_DASHBOARD_TOKEN", "t" * 64)

    assert _remote_client(session_cookie).get("/api/v1/runs").status_code == 200


# ------------------------------------------------- no anonymous access


@requires_postgres
@pytest.mark.parametrize("path", PROTECTED)
def test_production_still_refuses_a_request_with_no_session(
    live_db, production, path
):
    """Fixing the 401 must not turn into allowing everyone.

    Same conditions as the test above minus the session: no operator token, a
    remote peer, nobody signed in. That must still be refused, or the fix has
    opened the API to the internet.
    """
    production.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)

    assert _remote_client().get(path).status_code == 401


@requires_postgres
def test_a_forged_session_cookie_is_still_refused(live_db, production):
    """The session is what authenticates now, so it has to be the real one."""
    production.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)

    for bad in ("", "not-a-session", "abc.def", "x" * 100):
        assert _remote_client(bad or None).get("/api/v1/runs").status_code == 401, (
            f"a cookie of {bad!r} was accepted"
        )


@requires_postgres
def test_a_revoked_session_stops_working_in_production_too(
    session_cookie, production
):
    """Sign-out has to still end access on the path that now grants it."""
    from archguard.dashboard import _sessions

    production.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    assert _remote_client(session_cookie).get("/api/v1/runs").status_code == 200

    _sessions.revoke(session_cookie)

    assert _remote_client(session_cookie).get("/api/v1/runs").status_code == 401


# ------------------------------------------------- development is unchanged


@requires_postgres
def test_localhost_development_still_works_without_any_token(live_db, monkeypatch):
    """The loopback fallback is what makes `docker compose up` usable.

    Not production, no OAuth app, no operator token, peer is loopback: the
    request is signed in as the local development account. This is deliberate
    and must survive the fix -- it is the only reason a fresh checkout has a
    working dashboard.
    """
    from fastapi.testclient import TestClient

    from archguard.dashboard.app import app

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("SESSION_SECRET", TEST_SESSION_SECRET)

    assert TestClient(app).get("/api/v1/runs").status_code == 200


@requires_postgres
def test_an_operator_bearer_token_still_works(live_db, production):
    """The credential's actual purpose: reaching the API as an operator, with
    no browser and no session."""
    production.setenv("ARCHGUARD_DASHBOARD_TOKEN", "t" * 64)

    response = _remote_client().get(
        "/api/v1/runs", headers={"Authorization": f"Bearer {'t' * 64}"}
    )

    # Past `check_token`. `current_user` then refuses it, because a bearer token
    # identifies an operator and not an account, and every data route is scoped
    # to one -- which is the tenancy rule and not this test's subject.
    assert response.status_code in (200, 401)
    assert "Invalid or missing token" not in response.text
