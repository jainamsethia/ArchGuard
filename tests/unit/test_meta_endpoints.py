"""Liveness, readiness and metrics.

``/health`` returned 200 whenever the process was alive, and it was the only
one -- so a platform health check pointed at it reported a service as healthy
while its database was unreachable and every request was failing. That is worse
than having no health check, because it stops the platform restarting or
rolling back.

The split is what these tests pin, and the direction of each answer matters:
liveness must stay green when a dependency is down (a failing liveness check
kills the container, turning one outage into a restart storm), and readiness
must go red (so the load balancer stops sending traffic).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from archguard.dashboard.app import app
from archguard.dashboard.routes import meta
from tests.db_fixtures import requires_postgres

client = TestClient(app)


# ------------------------------------------------------------- liveness


def test_health_is_200_and_names_the_build():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["uptime_seconds"] >= 0


def test_health_stays_green_when_the_database_is_down(monkeypatch):
    """The whole point of separating it.

    A liveness check that fails on a database blip gets every container killed
    at once, taking down the instances that were still serving.
    """

    async def _broken():
        raise RuntimeError("database is gone")

    monkeypatch.setitem(meta.CHECKS, "database", _broken)
    assert client.get("/health").status_code == 200


def test_health_needs_no_authentication():
    """Platform probes carry no session."""
    from archguard.dashboard._rate_limit import reset_rate_limits

    reset_rate_limits()
    remote = TestClient(app, client=("203.0.113.9", 5555))
    assert remote.get("/health").status_code == 200


# ------------------------------------------------------------ readiness


@requires_postgres
def test_ready_is_200_when_everything_answers(live_db):
    response = client.get("/ready")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True
    assert set(body["checks"]) == {"database", "redis", "git", "data_dir"}


def test_ready_is_503_when_a_dependency_fails(monkeypatch):
    async def _broken():
        return False, "ConnectionRefusedError"

    monkeypatch.setitem(meta.CHECKS, "database", _broken)
    response = client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["checks"]["database"]["ok"] is False


def test_ready_runs_every_check_even_after_one_fails(monkeypatch):
    """An operator wants the whole picture.

    Stopping at the first failure means finding out about the second one after
    fixing the first, which is two deploys instead of one.
    """
    called: list[str] = []

    def _recorder(name: str):
        async def _check():
            called.append(name)
            return False, "down"

        return _check

    for name in list(meta.CHECKS):
        monkeypatch.setitem(meta.CHECKS, name, _recorder(name))

    response = client.get("/ready")
    assert response.status_code == 503
    assert set(called) == set(meta.CHECKS)


def test_a_check_that_raises_is_reported_not_propagated(monkeypatch):
    """A readiness endpoint that 500s tells the platform nothing useful."""

    async def _explodes():
        raise ValueError("boom")

    monkeypatch.setitem(meta.CHECKS, "redis", _explodes)
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["redis"]["detail"] == "ValueError"


def test_a_failure_detail_does_not_leak_the_connection_string(monkeypatch):
    """A connection error's message contains the URL, and the URL the password."""

    async def _leaky():
        raise RuntimeError(
            "could not connect to postgresql://archguard:hunter2@db:5432/x"
        )

    monkeypatch.setitem(meta.CHECKS, "database", _leaky)
    body = client.get("/ready").text
    assert "hunter2" not in body
    assert "RuntimeError" in body


# -------------------------------------------------------------- metrics


def test_metrics_are_prometheus_text():
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "# HELP archguard_up" in body
    assert "# TYPE archguard_up gauge" in body
    assert "archguard_up 1" in body


def test_metrics_report_the_build_version():
    body = client.get("/metrics").text
    assert 'archguard_build_info{version=' in body


@requires_postgres
def test_metrics_count_jobs_by_status(seed_run):
    seed_run()
    body = client.get("/metrics").text
    assert 'archguard_jobs_total{status="complete"}' in body
    assert "archguard_database_up 1" in body


def test_metrics_survive_the_database_being_down(monkeypatch):
    """A metrics endpoint that 500s goes quiet exactly when it is needed.

    Queue depth and job counts are what an operator looks at during an
    incident, and an incident is when the database is most likely to be the
    thing that is broken.
    """
    import archguard.db.session as session_module

    def _broken():
        raise RuntimeError("database is gone")

    monkeypatch.setattr(session_module, "get_sessionmaker", _broken)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "archguard_database_up 0" in response.text
    # The process-level metrics still come through.
    assert "archguard_up 1" in response.text


def test_every_metric_declares_its_type():
    """Prometheus tolerates a missing TYPE; a dashboard querying it does not."""
    body = client.get("/metrics").text
    names = {
        line.split("{")[0].split(" ")[0]
        for line in body.splitlines()
        if line and not line.startswith("#")
    }
    declared = {
        line.split()[2] for line in body.splitlines() if line.startswith("# TYPE ")
    }
    assert names <= declared, f"undeclared metrics: {sorted(names - declared)}"


# --------------------------------------------------------------- Sentry


def test_error_reporting_is_a_no_op_without_a_dsn(monkeypatch, caplog):
    from archguard.observability.errors import configure_error_reporting

    monkeypatch.delenv("SENTRY_DSN", raising=False)
    with caplog.at_level("INFO"):
        configure_error_reporting()
    assert any("SENTRY_DSN is not set" in r.message for r in caplog.records)


def test_error_reporting_does_not_raise_when_the_sdk_is_missing(monkeypatch, caplog):
    """Telemetry failing must not stop the service starting."""
    import builtins

    from archguard.observability.errors import configure_error_reporting

    monkeypatch.setenv("SENTRY_DSN", "https://key@example.invalid/1")
    real_import = builtins.__import__

    def _no_sentry(name, *args, **kwargs):
        if name == "sentry_sdk":
            raise ImportError("no sentry_sdk")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_sentry)
    with caplog.at_level("WARNING"):
        configure_error_reporting()
    assert any("sentry-sdk is not installed" in r.message for r in caplog.records)


@pytest.mark.parametrize(
    "header", ["Cookie", "cookie", "Authorization", "X-API-Key"]
)
def test_credentials_are_scrubbed_before_a_report_leaves(header):
    """Sentry captures request data by default.

    This application's requests carry a session cookie and, on the OAuth
    callback, a single-use authorization code. Neither belongs in a third-party
    error tracker, and "we will configure scrubbing later" is how they get
    there.
    """
    from archguard.observability.errors import _scrub

    event = {"request": {"headers": {header: "secret-value"}}}
    scrubbed = _scrub(event, {})
    assert scrubbed["request"]["headers"][header] == "[redacted]"


def test_an_oauth_code_is_scrubbed_from_the_query_string():
    from archguard.observability.errors import _scrub

    event = {"request": {"query_string": "code=abc123&state=xyz"}}
    assert _scrub(event, {})["request"]["query_string"] == "[redacted]"


def test_cookies_are_scrubbed():
    from archguard.observability.errors import _scrub

    event = {"request": {"cookies": {"archguard_session": "abc.def"}}}
    assert _scrub(event, {})["request"]["cookies"] == "[redacted]"
