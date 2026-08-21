"""The production configuration gate.

Every case here is a setting that, misconfigured, produces no error -- only
quietly weaker behaviour. That is the whole argument for refusing to start:
a warning about something nobody reads is indistinguishable from silence.

Each test states what actually goes wrong, so a future reader deciding whether
a check is worth keeping can weigh it rather than guess.
"""

from __future__ import annotations

import pytest

from archguard.dashboard._config_check import (
    ConfigurationError,
    validate_configuration,
)

GOOD = {
    "ENVIRONMENT": "production",
    "ALLOWED_ORIGINS": "https://archguard.example",
    "SESSION_SECRET": "s" * 64,
    "ARCHGUARD_DASHBOARD_TOKEN": "t" * 64,
    "GITHUB_OAUTH_CLIENT_ID": "client-id",
    "GITHUB_OAUTH_CLIENT_SECRET": "client-secret",
    "DATABASE_URL": "postgresql+asyncpg://u:p@127.0.0.1:5432/db",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
    "ARCHGUARD_TRUSTED_PROXY_IPS": "*",
}


@pytest.fixture
def production(monkeypatch, tmp_path):
    """A production environment with nothing wrong with it."""
    for key, value in GOOD.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", raising=False)
    monkeypatch.setenv("ARCHGUARD_DATA_DIR", str(tmp_path / "data"))
    return monkeypatch


def test_a_correct_production_configuration_starts(production):
    validate_configuration()


def test_development_is_not_gated(monkeypatch, tmp_path):
    """`make dev` must not need a GitHub OAuth app and a Redis.

    A check that made local development fail would be switched off within a
    week, and then it would not be protecting production either.
    """
    monkeypatch.setenv("ENVIRONMENT", "development")
    for key in GOOD:
        if key != "ENVIRONMENT":
            monkeypatch.delenv(key, raising=False)
    validate_configuration()


def test_an_unset_environment_is_not_gated(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    validate_configuration()


# --------------------------------------------------------------- CORS


def test_wildcard_origins_are_refused(production):
    """With credentialed CORS, '*' makes Starlette echo any Origin back.

    Every authenticated endpoint then answers any site on the internet with the
    user's cookie attached. Nothing validated this variable before.
    """
    production.setenv("ALLOWED_ORIGINS", "https://archguard.example,*")
    with pytest.raises(ConfigurationError, match=r"ALLOWED_ORIGINS contains '\*'"):
        validate_configuration()


def test_unset_origins_are_refused(production):
    """The default includes localhost, which a deployed site should not accept."""
    production.delenv("ALLOWED_ORIGINS", raising=False)
    with pytest.raises(ConfigurationError, match="ALLOWED_ORIGINS is not set"):
        validate_configuration()


def test_a_plaintext_origin_is_refused(production):
    production.setenv("ALLOWED_ORIGINS", "http://archguard.example")
    with pytest.raises(ConfigurationError, match="plaintext origin"):
        validate_configuration()


def test_plaintext_localhost_is_allowed(production):
    """Tunnels and local proxies are a real setup; the risk is the network."""
    production.setenv(
        "ALLOWED_ORIGINS", "https://archguard.example,http://localhost:8000"
    )
    validate_configuration()


# ------------------------------------------------------------ secrets


def test_a_missing_session_secret_is_refused(production):
    production.delenv("SESSION_SECRET", raising=False)
    with pytest.raises(ConfigurationError, match="SESSION_SECRET is not set"):
        validate_configuration()


def test_a_short_session_secret_is_refused(production):
    production.setenv("SESSION_SECRET", "hunter2")
    with pytest.raises(ConfigurationError, match="only 7 characters"):
        validate_configuration()


def test_reusing_the_ops_token_as_the_session_secret_is_refused(production):
    """They were one secret once, and that coupling is what this prevents.

    Sharing them means rotating the operator credential signs out every user,
    and anyone who learns it can forge a session for any account.
    """
    production.setenv("SESSION_SECRET", "x" * 64)
    production.setenv("ARCHGUARD_DASHBOARD_TOKEN", "x" * 64)
    with pytest.raises(ConfigurationError, match="same value"):
        validate_configuration()


# -------------------------------------------------------------- OAuth


def test_missing_oauth_credentials_are_refused(production):
    """Without them nobody can sign in, and every data endpoint 401s.

    This is also the gate the development fallback in ``_identity`` relies on:
    that path is open only while no OAuth app is configured, so production
    reaching it is exactly what this prevents.
    """
    production.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
    with pytest.raises(ConfigurationError, match="GITHUB_OAUTH_CLIENT_ID"):
        validate_configuration()


# --------------------------------------------------- backing services


def test_a_missing_database_url_is_refused(production):
    production.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ConfigurationError, match="DATABASE_URL is not set"):
        validate_configuration()


def test_a_missing_redis_url_is_refused(production):
    """Per-process sessions mean every deploy signs everyone out."""
    production.delenv("REDIS_URL", raising=False)
    with pytest.raises(ConfigurationError, match="REDIS_URL is not set"):
        validate_configuration()


# --------------------------------------------------------------- proxy


def test_unset_trusted_proxies_are_refused(production):
    """Otherwise every user shares one rate-limit bucket. D9.

    railway.toml never set this, so on Railway a single client could lock out
    the whole service -- including the endpoints that cost money per call.
    """
    production.delenv("ARCHGUARD_TRUSTED_PROXY_IPS", raising=False)
    with pytest.raises(ConfigurationError, match="ARCHGUARD_TRUSTED_PROXY_IPS"):
        validate_configuration()


# ------------------------------------------------------- data directory


def test_an_unwritable_data_directory_is_refused(production, tmp_path):
    """E8. The audit logger swallows write failures, so nothing else would say.

    A root-owned persistent disk under a container running as uid 1000 makes
    every write fail silently for the life of the deployment.
    """
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("I am a file", encoding="utf-8")
    production.setenv("ARCHGUARD_DATA_DIR", str(blocker))
    with pytest.raises(ConfigurationError, match="not writable"):
        validate_configuration()


# ------------------------------------------------------- allow-remote


def test_allow_remote_is_refused_in_production(production):
    production.setenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", "1")
    with pytest.raises(ConfigurationError, match="ALLOW_REMOTE"):
        validate_configuration()


# --------------------------------------------------------- reporting


def test_every_problem_is_reported_at_once(production):
    """Not the first one.

    Fixing a deployment one restart at a time, five minutes apart, is how a
    ten-minute configuration job becomes an afternoon.
    """
    production.delenv("SESSION_SECRET", raising=False)
    production.delenv("REDIS_URL", raising=False)
    production.delenv("ARCHGUARD_TRUSTED_PROXY_IPS", raising=False)

    with pytest.raises(ConfigurationError) as exc:
        validate_configuration()

    message = str(exc.value)
    assert "3 production configuration problem(s)" in message
    assert "SESSION_SECRET" in message
    assert "REDIS_URL" in message
    assert "ARCHGUARD_TRUSTED_PROXY_IPS" in message


def test_the_message_says_what_to_do(production):
    """An error naming a variable but not the fix costs a search each time."""
    production.delenv("SESSION_SECRET", raising=False)
    with pytest.raises(ConfigurationError) as exc:
        validate_configuration()
    assert "secrets.token_hex(32)" in str(exc.value)
