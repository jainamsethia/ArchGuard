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
    SHARED_CHECKS,
    ConfigurationError,
    Role,
    checks_for,
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


# --------------------------------------------------- roles: web vs worker


"""The worker is not a small web process.

Four of these checks describe HTTP behaviour. The worker serves no port, so
applying them there refused to start a correctly configured worker until an
operator copied a CORS origin list and an OAuth client secret onto a process
that reads neither -- which is not defence in depth, it is credentials in one
more place.
"""

WORKER_REQUIRED = {
    "ENVIRONMENT": "production",
    "DATABASE_URL": "postgresql+asyncpg://u:p@127.0.0.1:5432/db",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
}


@pytest.fixture
def worker_production(monkeypatch, tmp_path):
    """A worker with everything it genuinely needs and nothing it does not."""
    for key in GOOD:
        monkeypatch.delenv(key, raising=False)
    for key, value in WORKER_REQUIRED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", raising=False)
    monkeypatch.setenv("ARCHGUARD_DATA_DIR", str(tmp_path / "data"))
    return monkeypatch


def test_a_production_web_process_still_fails_without_its_web_settings(production):
    """The point of splitting the roles is not to let the web off anything."""
    production.delenv("ALLOWED_ORIGINS", raising=False)
    production.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
    production.delenv("GITHUB_OAUTH_CLIENT_SECRET", raising=False)
    production.delenv("ARCHGUARD_TRUSTED_PROXY_IPS", raising=False)

    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(Role.WEB)

    message = str(exc.value)
    assert "ALLOWED_ORIGINS" in message
    assert "GITHUB_OAUTH_CLIENT_ID" in message
    assert "ARCHGUARD_TRUSTED_PROXY_IPS" in message


def test_a_worker_starts_without_the_web_only_settings(worker_production):
    """A database, a queue and a writable directory is the whole worker
    contract. It has no origin to allow and no visitor to sign in."""
    validate_configuration(Role.WORKER)


def test_a_worker_needs_no_session_secret_or_operator_token(worker_production):
    """Traced, not assumed: SESSION_SECRET is the session-cookie HMAC and
    ARCHGUARD_DASHBOARD_TOKEN authenticates operator API calls. Neither is read
    anywhere the worker executes, so neither belongs in its manifest."""
    worker_production.delenv("SESSION_SECRET", raising=False)
    worker_production.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    validate_configuration(Role.WORKER)


@pytest.mark.parametrize("missing", ["DATABASE_URL", "REDIS_URL"])
def test_a_worker_still_fails_without_what_it_genuinely_needs(
    worker_production, missing
):
    """Without Redis it has no queue to read; without PostgreSQL it cannot
    record the analysis it just performed."""
    worker_production.delenv(missing, raising=False)
    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(Role.WORKER)
    assert missing in str(exc.value)


def test_a_worker_still_fails_on_an_unwritable_data_directory(
    worker_production, tmp_path
):
    """The worker is the process that writes the audit log."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    worker_production.setenv("ARCHGUARD_DATA_DIR", str(blocker / "under-a-file"))

    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(Role.WORKER)
    assert "not writable" in str(exc.value)


def test_the_remote_access_override_is_refused_in_every_role(worker_production):
    """Shared on purpose. It only changes the HTTP auth path, so on a worker it
    does nothing -- but a production environment carrying it at all is
    misconfigured, and whichever process starts first should say so."""
    worker_production.setenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", "1")
    with pytest.raises(ConfigurationError) as exc:
        validate_configuration(Role.WORKER)
    assert "ARCHGUARD_DASHBOARD_ALLOW_REMOTE" in str(exc.value)


# ------------------------------------------- the role cannot become a bypass


def test_every_role_runs_every_shared_check():
    """A role adds checks. It must not be able to subtract one."""
    for role in Role:
        assert set(SHARED_CHECKS).issubset(set(checks_for(role))), role


def test_an_unknown_role_is_refused_rather_than_treated_as_empty(production):
    """A typo resolving to "no checks" would turn the parameter that describes
    a process into a way of switching the gate off."""
    for bogus in ("web", "worker", "", None, "WEB"):
        with pytest.raises(ConfigurationError) as exc:
            validate_configuration(bogus)  # type: ignore[arg-type]
        assert "Unknown process role" in str(exc.value)


def test_an_unknown_role_is_refused_even_outside_production(monkeypatch):
    """Otherwise the typo is found in production, which is the worst place."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    with pytest.raises(ConfigurationError):
        validate_configuration("worker")  # type: ignore[arg-type]


def test_the_default_role_is_the_stricter_one(production):
    """A caller that forgets to say which process it is must get more checks,
    not fewer: the failure mode is a false refusal, never a silent pass."""
    production.delenv("ALLOWED_ORIGINS", raising=False)
    with pytest.raises(ConfigurationError) as exc:
        validate_configuration()
    assert "ALLOWED_ORIGINS" in str(exc.value)
    assert set(checks_for(Role.WEB)) >= set(checks_for(Role.WORKER))
