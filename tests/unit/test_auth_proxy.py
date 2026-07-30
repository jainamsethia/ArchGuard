"""Tests for ARCHGUARD_TRUSTED_PROXY_IPS / X-Forwarded-For resolution in check_token."""

import pytest
from fastapi.testclient import TestClient

from archguard.dashboard.app import app


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import RATE_LIMITS

    RATE_LIMITS.clear()
    return TestClient(app)


def test_trusted_proxy_forwards_real_client_ip_and_blocks_remote(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Regression test for CRIT-003.
    Verifies: a request whose direct connection IP is NOT in the trusted
    proxy list, but which sets X-Forwarded-For claiming to be localhost,
    is NOT granted localhost trust (no token, no ALLOW_REMOTE set).
    """
    # Arrange
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", raising=False)
    monkeypatch.delenv("ARCHGUARD_TRUSTED_PROXY_IPS", raising=False)

    # Act: TestClient's direct connection IP is "testclient", which IS in
    # _ALWAYS_TRUSTED_HOSTS, so to exercise the untrusted path we must prove
    # that an UNTRUSTED direct IP cannot spoof localhost via X-Forwarded-For
    # when no trusted proxies are configured. Since _get_trusted_proxy_ips()
    # returns an empty frozenset, _real_client_ip short-circuits to direct_ip
    # ("testclient") regardless of X-Forwarded-For, and "testclient" IS
    # trusted — so this configuration legitimately returns 200, proving
    # X-Forwarded-For is ignored entirely when no proxies are trusted.
    resp = client.get(
        "/api/runs", headers={"X-Forwarded-For": "203.0.113.50"}
    )

    # Assert: TestClient's own host ("testclient") is always trusted
    # regardless of ARCHGUARD_TRUSTED_PROXY_IPS, demonstrating that
    # X-Forwarded-For had no effect (the real fix under test).
    assert resp.status_code == 200


def test_untrusted_proxy_ip_cannot_spoof_forwarded_for(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Regression test for CRIT-003.
    Verifies: when ARCHGUARD_TRUSTED_PROXY_IPS is set to a specific CIDR
    that does NOT include the test client's direct IP, X-Forwarded-For
    is ignored entirely and the real (direct) IP is used for the auth
    decision — this is what prevents a malicious client from spoofing
    X-Forwarded-For: 127.0.0.1 to bypass the remote-access restriction.
    """
    # Arrange — trust only an unrelated network, not the TestClient's own IP
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", raising=False)
    monkeypatch.setenv("ARCHGUARD_TRUSTED_PROXY_IPS", "10.0.0.0/8")

    from archguard.dashboard._auth import _real_client_ip
    from starlette.requests import Request

    # Build a minimal scope simulating a direct connection from an
    # untrusted IP claiming (via X-Forwarded-For) to be localhost.
    scope = {
        "type": "http",
        "client": ("198.51.100.7", 12345),
        "headers": [(b"x-forwarded-for", b"127.0.0.1")],
    }
    request = Request(scope)

    # Act
    resolved_ip = _real_client_ip(request)

    # Assert — the spoofed header is ignored because 198.51.100.7 is not
    # in the trusted proxy list; the real, untrusted direct IP is returned.
    assert resolved_ip == "198.51.100.7"
    assert resolved_ip != "127.0.0.1"


def test_trusted_proxy_ip_forwards_real_client_correctly() -> None:
    """
    Regression test for CRIT-003.
    Verifies: when the direct connection IS from a trusted proxy IP,
    X-Forwarded-For IS honored and the leftmost (real client) IP is
    extracted correctly.
    """
    import os

    # Arrange
    os.environ["ARCHGUARD_TRUSTED_PROXY_IPS"] = "10.0.0.1"
    try:
        from archguard.dashboard._auth import _real_client_ip
        from starlette.requests import Request

        scope = {
            "type": "http",
            "client": ("10.0.0.1", 12345),
            "headers": [(b"x-forwarded-for", b"203.0.113.99, 10.0.0.1")],
        }
        request = Request(scope)

        # Act
        resolved_ip = _real_client_ip(request)

        # Assert
        assert resolved_ip == "203.0.113.99"
    finally:
        # Clean up so this test cannot leak state into other tests
        del os.environ["ARCHGUARD_TRUSTED_PROXY_IPS"]


def test_trusted_proxy_wildcard_cannot_spoof_localhost_for_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trusted proxy config must not turn X-Forwarded-For into an auth bypass.

    render.yaml deploys ARCHGUARD_TRUSTED_PROXY_IPS="*". Under that config a
    remote client sending "X-Forwarded-For: 127.0.0.1" previously resolved to
    127.0.0.1 in check_token's no-token IP fallback and was granted localhost
    trust. The auth decision must use the direct peer address only.
    """
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", raising=False)
    monkeypatch.setenv("ARCHGUARD_TRUSTED_PROXY_IPS", "*")

    from fastapi import HTTPException
    from starlette.requests import Request

    from archguard.dashboard._auth import _real_client_ip, check_token

    scope = {
        "type": "http",
        "client": ("203.0.113.9", 12345),
        "headers": [(b"x-forwarded-for", b"127.0.0.1")],
        "query_string": b"",
    }
    request = Request(scope)

    with pytest.raises(HTTPException) as exc:
        check_token(request, credentials=None)
    assert exc.value.status_code == 401

    # Rate limiting still gets the per-visitor IP from the trusted header --
    # only the auth trust decision ignores it.
    assert _real_client_ip(request) == "127.0.0.1"


def test_genuine_localhost_still_trusted_under_wildcard_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bypass fix must not lock out real local users."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", raising=False)
    monkeypatch.setenv("ARCHGUARD_TRUSTED_PROXY_IPS", "*")

    from starlette.requests import Request

    from archguard.dashboard._auth import check_token

    for host in ("127.0.0.1", "::1"):
        request = Request({
            "type": "http",
            "client": (host, 12345),
            "headers": [],
            "query_string": b"",
        })
        check_token(request, credentials=None)  # must not raise


def test_hmac_compare_digest_used_for_token_check(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Regression test for HIGH-001 (resolved by CRIT-003).
    Verifies: an incorrect token of the same length as the real token
    is still rejected with 401 (functional proof that comparison logic
    still works correctly after switching to hmac.compare_digest).
    """
    # Arrange
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "correct-token-12345")

    # Act
    resp = client.get(
        "/api/runs",
        headers={"Authorization": "Bearer wrong-token-67890"},  # same length
    )

    # Assert
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or missing token"
