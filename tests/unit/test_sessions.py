"""Session cookies: issued for a user, verified before the store is touched.

Replaces ``test_cookie_auth.py``, which tested the shared-token exchange these
sessions replace. Two things about the old model are worth stating, because
this file is what pins their absence:

* the HMAC key was ``ARCHGUARD_DASHBOARD_TOKEN``, so the operator credential
  and every browser session shared one secret -- rotating the ops token signed
  everyone out, and anyone who learned it could forge any session;
* a session identified nobody, so no endpoint below it could filter by owner.
"""

from __future__ import annotations

import pytest

from archguard.dashboard import _sessions

SECRET = "a" * 64
OTHER_SECRET = "b" * 64


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    _sessions.reset_sessions()
    yield
    _sessions.reset_sessions()


def test_a_session_resolves_to_the_user_it_was_issued_for():
    assert _sessions.resolve(_sessions.issue(42)) == 42


def test_two_users_get_distinguishable_sessions():
    a, b = _sessions.issue(1), _sessions.issue(2)
    assert a != b
    assert _sessions.resolve(a) == 1
    assert _sessions.resolve(b) == 2


def test_a_cookie_signed_with_a_different_secret_is_rejected(monkeypatch):
    """Rotating SESSION_SECRET invalidates every outstanding session."""
    cookie = _sessions.issue(7)
    monkeypatch.setenv("SESSION_SECRET", OTHER_SECRET)
    assert _sessions.resolve(cookie) is None


def test_a_tampered_signature_is_rejected():
    cookie = _sessions.issue(7)
    session_id, _, sig = cookie.partition(".")
    flipped = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    assert _sessions.resolve(f"{session_id}.{flipped}") is None


@pytest.mark.parametrize(
    "value", ["", "no-dot-in-this-value", ".", "..", "abc.", ".abc"]
)
def test_malformed_cookies_are_rejected(value):
    assert _sessions.resolve(value) is None


def test_a_session_id_alone_is_not_enough():
    """The store is only consulted after the signature verifies.

    Otherwise a valid session id -- which is the half of the cookie that gets
    logged, cached and copied around -- would be a credential on its own.
    """
    cookie = _sessions.issue(7)
    assert _sessions.resolve(cookie.partition(".")[0]) is None


def test_revoking_ends_the_session():
    cookie = _sessions.issue(7)
    assert _sessions.resolve(cookie) == 7
    _sessions.revoke(cookie)
    assert _sessions.resolve(cookie) is None


def test_revoking_a_malformed_cookie_does_not_raise():
    """Signing out has to work even when the cookie is junk."""
    _sessions.revoke("")
    _sessions.revoke("garbage")


def test_no_secret_means_no_session(monkeypatch):
    """Never falls back to another secret, however convenient that would be."""
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "ops-token")
    with pytest.raises(_sessions.SessionSecretMissingError):
        _sessions.issue(1)
    assert _sessions.resolve("anything.at-all") is None


def test_the_local_fallback_is_bounded(monkeypatch):
    """Without Redis the in-process map must not grow without limit."""
    monkeypatch.setattr(_sessions, "_LOCAL_MAX", 5)
    monkeypatch.setattr("archguard.dashboard._sessions.get_redis", lambda: None)
    _sessions.reset_sessions()
    cookies = [_sessions.issue(i) for i in range(20)]
    assert len(_sessions._LOCAL) <= 5
    # The most recent ones survive; the evicted ones simply stop resolving,
    # which signs those users out rather than corrupting anything.
    assert _sessions.resolve(cookies[-1]) == 19
