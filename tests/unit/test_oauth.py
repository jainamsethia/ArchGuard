"""The GitHub OAuth flow.

The network is stubbed at the ``httpx`` boundary -- this is about the parts
that are ours to get wrong (CSRF state, what reaches the browser, which field
identifies the account), not about GitHub's endpoints answering correctly.
"""

from __future__ import annotations

import httpx
import pytest

from archguard.dashboard import _oauth, _sessions
from archguard.dashboard.app import app
from tests.db_fixtures import requires_postgres

SECRET = "d" * 64


@pytest.fixture(autouse=True)
def oauth_configured(monkeypatch):
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.delenv("GITHUB_OAUTH_REDIRECT_URI", raising=False)
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import reset_rate_limits

    reset_rate_limits()
    _sessions.reset_sessions()
    yield
    _sessions.reset_sessions()


def _client():
    from fastapi.testclient import TestClient

    return TestClient(app, follow_redirects=False)


# --------------------------------------------------------------- start


def test_login_redirects_to_github_with_state():
    response = _client().get("/auth/github")
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(_oauth.AUTHORIZE_URL)
    assert "client_id=client-id" in location
    assert "scope=read%3Auser" in location
    assert "state=" in location
    assert _oauth.STATE_COOKIE in response.cookies


def test_the_client_secret_never_reaches_the_browser():
    response = _client().get("/auth/github")
    assert "client-secret" not in response.headers["location"]
    assert "client-secret" not in str(response.headers)


def test_the_state_cookie_is_httponly_and_lax():
    """Lax, not Strict.

    The browser arrives back from github.com by cross-site navigation, and a
    Strict cookie is not sent on one -- so a Strict state cookie would make the
    callback's CSRF check fail every single time.
    """
    header = _client().get("/auth/github").headers["set-cookie"]
    assert "httponly" in header.lower()
    assert "samesite=lax" in header.lower()


def test_only_read_user_is_requested():
    """A wider scope would ask for access the product has no use for.

    ArchGuard clones public repositories over anonymous HTTPS and never acts on
    the user's behalf. Private repositories (P3-3) are a GitHub App
    installation, not a bigger OAuth scope.
    """
    assert _oauth.SCOPE == "read:user"


def test_sign_in_is_503_when_not_configured(monkeypatch):
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_SECRET", raising=False)
    response = _client().get("/auth/github")
    assert response.status_code == 503


# ------------------------------------------------------------ callback


def test_callback_without_state_is_rejected():
    """The CSRF check.

    Without it, an attacker completes the flow with their own authorization
    code in the victim's browser and silently binds the victim's session to the
    attacker's account -- so anything the victim then analyses lands in the
    attacker's history.
    """
    response = _client().get("/auth/github/callback?code=abc&state=anything")
    assert response.status_code == 400


def test_callback_with_a_mismatched_state_is_rejected():
    client = _client()
    client.get("/auth/github")  # sets a real state cookie
    response = client.get("/auth/github/callback?code=abc&state=not-the-one")
    assert response.status_code == 400


def test_cancelling_returns_to_the_landing_page():
    """Pressing Cancel on GitHub is a normal outcome, not an error page."""
    response = _client().get("/auth/github/callback?error=access_denied")
    assert response.status_code == 302
    assert response.headers["location"].startswith("/?auth=cancelled")


@requires_postgres
def test_a_successful_callback_creates_an_account_and_a_session(
    live_db, monkeypatch
):
    async def _fake_exchange(code, redirect_uri=None):
        assert code == "the-code"
        return "gh-access-token"

    async def _fake_identity(token):
        assert token == "gh-access-token"
        return _oauth.GitHubIdentity(
            github_id=99001, login="octocat", avatar_url="https://example/a.png"
        )

    monkeypatch.setattr(_oauth, "exchange_code", _fake_exchange)
    monkeypatch.setattr(_oauth, "fetch_identity", _fake_identity)

    client = _client()
    start = client.get("/auth/github")
    state = start.cookies[_oauth.STATE_COOKIE]

    response = client.get(f"/auth/github/callback?code=the-code&state={state}")
    assert response.status_code == 302
    assert response.headers["location"] == "/"

    cookie = response.cookies.get(_sessions.COOKIE_NAME)
    assert cookie, "a session cookie must be issued"
    user_id = _sessions.resolve(cookie)
    assert user_id is not None

    status = client.get("/api/v1/auth/status")
    assert status.json()["authenticated"] is True
    assert status.json()["user"]["login"] == "octocat"


@requires_postgres
def test_signing_in_twice_reuses_the_same_account(live_db, monkeypatch):
    """Matched on github_id, so a renamed login is still the same person."""
    from archguard.db import store
    from archguard.db.session import session_scope
    from tests.db_fixtures import _run

    async def _twice() -> tuple[int, int, str]:
        async with session_scope() as session:
            first = await store.upsert_user(session, github_id=99002, login="before")
            first_id = first.id
        async with session_scope() as session:
            second = await store.upsert_user(session, github_id=99002, login="after")
            return first_id, second.id, second.login

    first_id, second_id, login = _run(_twice())
    assert first_id == second_id, "a renamed login must not create a second account"
    assert login == "after", "the login is refreshed, because a stale one is wrong"


@requires_postgres
def test_a_renamed_login_does_not_inherit_someone_elses_history(live_db):
    """Two different accounts that have held the same login stay separate."""
    from archguard.db import store
    from archguard.db.session import session_scope
    from tests.db_fixtures import _run

    async def _two() -> tuple[int, int]:
        async with session_scope() as session:
            a = await store.upsert_user(session, github_id=1, login="shared-name")
            a_id = a.id
        async with session_scope() as session:
            b = await store.upsert_user(session, github_id=2, login="shared-name")
            return a_id, b.id

    a_id, b_id = _run(_two())
    assert a_id != b_id


# ---------------------------------------------------------- exchange


@pytest.mark.asyncio
async def test_an_error_payload_from_the_token_endpoint_raises(monkeypatch):
    """GitHub answers 200 with {"error": ...}, so the status code is not enough."""

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {"error": "bad_verification_code"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client())
    with pytest.raises(_oauth.OAuthError, match="bad_verification_code"):
        await _oauth.exchange_code("stale-code")


@pytest.mark.asyncio
async def test_a_user_payload_without_a_numeric_id_raises(monkeypatch):
    """``id`` is the identity. A payload lacking one is unusable, not a default."""

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {"login": "octocat"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client())
    with pytest.raises(_oauth.OAuthError):
        await _oauth.fetch_identity("token")
