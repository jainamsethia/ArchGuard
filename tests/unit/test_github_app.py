"""Tests for GitHub App installation auth (P3-3).

No live credentials are involved. The key below is generated in-process for the
test run: it is a real RSA key, so the RS256 signing path is genuinely
exercised, and it authenticates nothing because GitHub has never seen it.
"""

from __future__ import annotations

import base64
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from archguard.dashboard import _github_app


@pytest.fixture
def rsa_key_pair() -> tuple[str, str]:
    """A throwaway RSA key pair, as PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


@pytest.fixture(autouse=True)
def _clear_cache():
    _github_app.forget_cached_tokens()
    yield
    _github_app.forget_cached_tokens()


def _configure(monkeypatch, private_pem: str, app_id: str = "12345") -> None:
    monkeypatch.setenv("GITHUB_APP_ID", app_id)
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", private_pem)


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


def test_absent_configuration_is_not_an_error(monkeypatch):
    """A deployment without an App is the normal case, not a broken one."""
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    assert _github_app.is_configured() is False


def test_half_a_configuration_does_not_count_as_configured(monkeypatch):
    """An id with no key would fail at signing time, which is far too late."""
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    assert _github_app.is_configured() is False


def test_a_pem_with_escaped_newlines_is_accepted(monkeypatch, rsa_key_pair):
    """Most dashboards cannot hold a multi-line secret and escape it instead."""
    private_pem, _ = rsa_key_pair
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", private_pem.replace("\n", "\\n"))
    assert "-----BEGIN" in _github_app.private_key()
    assert "\\n" not in _github_app.private_key()


def test_a_base64_pem_is_accepted(monkeypatch, rsa_key_pair):
    """The other shape people reach for, once escaping has bitten them."""
    private_pem, _ = rsa_key_pair
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv(
        "GITHUB_APP_PRIVATE_KEY", base64.b64encode(private_pem.encode()).decode()
    )
    assert _github_app.private_key().startswith("-----BEGIN")


# --------------------------------------------------------------------------
# the JWT
# --------------------------------------------------------------------------


def test_the_jwt_verifies_against_the_public_key(monkeypatch, rsa_key_pair):
    """The whole point: GitHub verifies this signature with the public half."""
    private_pem, public_pem = rsa_key_pair
    _configure(monkeypatch, private_pem)

    token = _github_app.app_jwt()
    claims = jwt.decode(token, public_pem, algorithms=["RS256"], options={"verify_aud": False})

    assert claims["iss"] == "12345"


def test_the_jwt_stays_inside_githubs_ten_minute_ceiling(monkeypatch, rsa_key_pair):
    """GitHub rejects a longer-lived App JWT outright."""
    private_pem, public_pem = rsa_key_pair
    _configure(monkeypatch, private_pem)

    now = time.time()
    claims = jwt.decode(
        _github_app.app_jwt(now=now),
        public_pem,
        algorithms=["RS256"],
        options={"verify_aud": False},
    )
    assert claims["exp"] - claims["iat"] <= 600


def test_the_jwt_is_backdated_against_clock_skew(monkeypatch, rsa_key_pair):
    """A fast local clock otherwise yields a 401 that reads like a bad key."""
    private_pem, public_pem = rsa_key_pair
    _configure(monkeypatch, private_pem)

    now = time.time()
    claims = jwt.decode(
        _github_app.app_jwt(now=now),
        public_pem,
        algorithms=["RS256"],
        options={"verify_aud": False},
    )
    assert claims["iat"] < now


def test_an_unusable_key_names_the_setting(monkeypatch):
    """PyJWT's own message does not say which environment variable is wrong."""
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----junk")
    with pytest.raises(_github_app.GitHubAppError, match="GITHUB_APP_PRIVATE_KEY"):
        _github_app.app_jwt()


def test_signing_without_configuration_says_so(monkeypatch):
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    with pytest.raises(_github_app.GitHubAppError, match="not configured"):
        _github_app.app_jwt()


# --------------------------------------------------------------------------
# the API calls
# --------------------------------------------------------------------------


def _client_returning(response, *, record: dict | None = None):
    """A stand-in for httpx.AsyncClient, matching the convention in test_oauth."""

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kwargs):
            if record is not None:
                record["url"] = url
                record["headers"] = kwargs.get("headers", {})
            return response

        async def post(self, url, **kwargs):
            if record is not None:
                record["url"] = url
                record["json"] = kwargs.get("json")
                record["headers"] = kwargs.get("headers", {})
            return response

    return lambda **k: _Client()


class _Response:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_a_missing_installation_is_reported_as_not_installed(
    monkeypatch, rsa_key_pair
):
    """404 here means "not installed there", which is a user-fixable state."""
    private_pem, _ = rsa_key_pair
    _configure(monkeypatch, private_pem)
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(_Response(404, {})))

    with pytest.raises(_github_app.GitHubAppError, match="not installed"):
        await _github_app.installation_id_for("acme", "secret")


@pytest.mark.asyncio
async def test_the_installation_lookup_sends_the_jwt(monkeypatch, rsa_key_pair):
    private_pem, public_pem = rsa_key_pair
    _configure(monkeypatch, private_pem)
    record: dict = {}
    monkeypatch.setattr(
        httpx, "AsyncClient", _client_returning(_Response(200, {"id": 42}), record=record)
    )

    assert await _github_app.installation_id_for("acme", "widget") == 42
    assert record["url"].endswith("/repos/acme/widget/installation")

    bearer = record["headers"]["Authorization"].removeprefix("Bearer ")
    # It must be the App JWT, not something else: verify it against the key.
    jwt.decode(bearer, public_pem, algorithms=["RS256"], options={"verify_aud": False})


@pytest.mark.asyncio
async def test_a_token_is_scoped_to_the_repository_being_analysed(
    monkeypatch, rsa_key_pair
):
    """An installation may cover many repositories; one clone needs one."""
    private_pem, _ = rsa_key_pair
    _configure(monkeypatch, private_pem)
    record: dict = {}
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        _client_returning(
            _Response(201, {"token": "ghs_secret", "expires_at": "2099-01-01T00:00:00Z"}),
            record=record,
        ),
    )

    minted = await _github_app.mint_installation_token(42, repositories=["widget"])
    assert minted.token == "ghs_secret"
    assert record["json"] == {"repositories": ["widget"]}


@pytest.mark.asyncio
async def test_a_response_without_a_token_raises(monkeypatch, rsa_key_pair):
    """Returning an empty string here would produce an unauthenticated clone."""
    private_pem, _ = rsa_key_pair
    _configure(monkeypatch, private_pem)
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(_Response(201, {})))

    with pytest.raises(_github_app.GitHubAppError, match="no token"):
        await _github_app.mint_installation_token(42)


def test_the_token_is_not_in_its_own_repr():
    """This object reaches log lines, exception context and test output."""
    token = _github_app.InstallationToken(token="ghs_secret", expires_at=0.0)
    assert "ghs_secret" not in repr(token)


def test_a_token_near_expiry_is_not_reused():
    """Reusing one that dies mid-clone looks exactly like a revoked App."""
    now = time.time()
    nearly_done = _github_app.InstallationToken(token="x", expires_at=now + 60)
    fresh = _github_app.InstallationToken(token="x", expires_at=now + 3600)

    assert nearly_done.is_usable(now=now) is False
    assert fresh.is_usable(now=now) is True


def test_an_unparseable_expiry_falls_back_to_the_documented_hour():
    """Neither "already expired" nor "never expires" is a safe reading."""
    assumed = _github_app._parse_expiry("not a timestamp")
    assert 3000 < assumed - time.time() <= 3600


# --------------------------------------------------------------------------
# the worker's use of it
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_app_configured_clones_anonymously(monkeypatch):
    """The overwhelmingly common case: no App, public repository, no API calls."""
    from archguard.worker.tasks import _installation_token

    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)

    def _explode(**kwargs):  # pragma: no cover - must never be reached
        raise AssertionError("an unconfigured App must not call GitHub")

    monkeypatch.setattr(httpx, "AsyncClient", _explode)

    assert await _installation_token("pallets", "flask") is None


@pytest.mark.asyncio
async def test_an_app_not_installed_there_clones_anonymously(monkeypatch, rsa_key_pair):
    """A public repository the owner never connected is not a failed job."""
    from archguard.worker.tasks import _installation_token

    private_pem, _ = rsa_key_pair
    _configure(monkeypatch, private_pem)
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(_Response(404, {})))

    assert await _installation_token("pallets", "flask") is None


@pytest.mark.asyncio
async def test_a_github_outage_does_not_fail_a_public_analysis(
    monkeypatch, rsa_key_pair
):
    """The token is an optimisation for public repositories; it may not be fatal."""
    from archguard.worker.tasks import _installation_token

    private_pem, _ = rsa_key_pair
    _configure(monkeypatch, private_pem)

    class _Broken:
        async def __aenter__(self):
            raise httpx.ConnectError("github is unreachable")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Broken())

    assert await _installation_token("pallets", "flask") is None


@pytest.mark.asyncio
async def test_an_installed_app_yields_a_token(monkeypatch, rsa_key_pair):
    """The path that actually enables a private clone."""
    from archguard.worker.tasks import _installation_token

    private_pem, _ = rsa_key_pair
    _configure(monkeypatch, private_pem)

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kwargs):
            return _Response(200, {"id": 42})

        async def post(self, url, **kwargs):
            return _Response(
                201, {"token": "ghs_minted", "expires_at": "2099-01-01T00:00:00Z"}
            )

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client())

    assert await _installation_token("acme", "secret") == "ghs_minted"
