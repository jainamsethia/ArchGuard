from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from archguard.dashboard.app import app
from tests.db_fixtures import requires_postgres

# Create a TestClient for our app
client = TestClient(app)


def test_dashboard_version_matches_package_version():
    import importlib.metadata

    from archguard.dashboard.app import app

    try:
        expected = importlib.metadata.version("archguard")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("archguard is not installed as a distribution (source checkout)")
    assert app.version == expected


@pytest.fixture
def mock_audit_logger(live_db):
    """An empty database behind the endpoint under test.

    These are auth and validation tests -- they care about status codes, not
    rows -- but the endpoint queries PostgreSQL, so it gets PostgreSQL. Stubbing
    the query here would mean the 200 path never proves the endpoint can serve
    a request at all.
    """
    yield None


@requires_postgres
def test_api_runs_no_token_configured(mock_audit_logger, monkeypatch):
    """Test that /api/runs returns 200 when no token is configured."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import reset_rate_limits

    reset_rate_limits()

    # We also mock the host to simulate it being localhost
    response = client.get("/api/runs")
    assert response.status_code == 200
    assert "runs" in response.json()


@requires_postgres
def test_api_runs_token_configured_no_auth(mock_audit_logger, monkeypatch):
    """Test that /api/runs returns 401 when ARCHGUARD_DASHBOARD_TOKEN is set and no auth header is provided."""
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "secret-token")

    response = client.get("/api/runs")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing token"


@requires_postgres
def test_api_runs_token_configured_with_correct_auth(mock_audit_logger, monkeypatch):
    """Test that /api/runs returns 200 with the correct bearer token."""
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "secret-token")

    response = client.get("/api/runs", headers={"Authorization": "Bearer secret-token"})
    assert response.status_code == 200
    assert "runs" in response.json()


@requires_postgres
def test_api_runs_token_configured_with_incorrect_auth(mock_audit_logger, monkeypatch):
    """Test that /api/runs returns 401 with an incorrect bearer token."""
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "secret-token")

    response = client.get("/api/runs", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing token"


from collections import namedtuple

ClientTuple = namedtuple("ClientTuple", ["host", "port"])


@requires_postgres
def test_api_runs_remote_no_token_401(mock_audit_logger, monkeypatch):
    """Test that remote IP without token returns 401."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import reset_rate_limits

    reset_rate_limits()
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", raising=False)

    with patch(
        "starlette.requests.Request.client", new_callable=MagicMock
    ) as mock_client:
        mock_client.host = "192.168.1.100"
        mock_client.port = 12345

        response = client.get("/api/runs")
        assert response.status_code == 401
        assert (
            "Dashboard requires ARCHGUARD_DASHBOARD_TOKEN" in response.json()["detail"]
        )


@requires_postgres
def test_api_runs_remote_with_token_200(mock_audit_logger, monkeypatch):
    """Test that remote IP with correct token returns 200."""
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "secret-token")

    with patch(
        "starlette.requests.Request.client", new_callable=MagicMock
    ) as mock_client:
        mock_client.host = "192.168.1.100"
        mock_client.port = 12345

        response = client.get(
            "/api/runs", headers={"Authorization": "Bearer secret-token"}
        )
        assert response.status_code == 200


@requires_postgres
def test_api_runs_limit_exceeds_max_returns_422(mock_audit_logger, monkeypatch):
    """Test that /api/runs returns 422 if limit > 500."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import reset_rate_limits

    reset_rate_limits()

    response = client.get("/api/runs?limit=999999")
    assert response.status_code == 422
    assert "less than or equal to 500" in response.json()["detail"][0]["msg"]


def test_response_includes_csp_header_without_hardcoded_localhost(monkeypatch) -> None:
    """
    Regression test for MED-001.
    Verifies: any response from the app includes a Content-Security-Policy
    header, and that header's connect-src does NOT contain the old
    hardcoded http://localhost:8000 origin that broke production deployments.
    """
    from fastapi.testclient import TestClient

    from archguard.dashboard.app import app

    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_ALLOW_REMOTE", raising=False)
    client = TestClient(app)

    # Act
    resp = client.get("/health")

    # Assert
    assert "Content-Security-Policy" in resp.headers
    csp = resp.headers["Content-Security-Policy"]
    assert "http://localhost:8000" not in csp
    assert "connect-src 'self'" in csp
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"


@requires_postgres
def test_api_runs_rate_limiting_returns_429(mock_audit_logger, monkeypatch):
    """The 51st request in a window is refused.

    Time is pinned for the duration. The limiter buckets by
    ``int(time.time()) // RATE_LIMIT_WINDOW``, so a real clock crossing a
    boundary mid-test resets the counter and the 51st request succeeds -- a true
    property of fixed-window limiting (a client can get 2x the limit across a
    boundary) but not the thing under test, and a flake that only shows up when
    the suite happens to start near a minute mark.
    """
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard import _rate_limit
    from archguard.dashboard._rate_limit import reset_rate_limits

    monkeypatch.setattr(_rate_limit.time, "time", lambda: 1_700_000_000.0)
    reset_rate_limits()

    for _ in range(50):
        response = client.get("/api/runs")
        assert response.status_code == 200

    response = client.get("/api/runs")
    assert response.status_code == 429
    assert response.json()["detail"] == "Too many requests"


@requires_postgres
def test_api_trends_invalid_module_returns_422(mock_audit_logger, monkeypatch):
    """Test that /api/trends/<invalid-chars> returns 422."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.dashboard._rate_limit import reset_rate_limits

    reset_rate_limits()
    response = client.get("/api/trends/invalid_@_module!")
    assert response.status_code == 422
    assert "pattern" in response.json()["detail"][0]["type"]

def test_csp_header_contains_nonce_and_no_unsafe_inline():
    """
    Regression test for ENH-003.
    Verifies: every response includes a CSP header with a nonce in script-src
    and does NOT contain 'unsafe-inline' in script-src.
    """
    response = client.get("/health")
    csp = response.headers.get("Content-Security-Policy", "")
    assert "script-src" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0], (
        "script-src must not contain 'unsafe-inline' after ENH-003"
    )
    import re
    nonce_match = re.search(r"'nonce-([a-f0-9]{32})'", csp)
    assert nonce_match is not None, f"No nonce found in CSP header: {csp!r}"

def test_csp_nonce_is_unique_per_request():
    """
    Verifies: each request generates a distinct nonce (no nonce reuse).
    """
    r1 = client.get("/health")
    r2 = client.get("/health")

    import re
    csp1 = r1.headers.get("Content-Security-Policy", "")
    csp2 = r2.headers.get("Content-Security-Policy", "")

    nonce1 = re.search(r"'nonce-([a-f0-9]{32})'", csp1)
    nonce2 = re.search(r"'nonce-([a-f0-9]{32})'", csp2)

    assert nonce1 is not None and nonce2 is not None
    assert nonce1.group(1) != nonce2.group(1), (
        "CSP nonce must be unique per request"
    )

def test_index_html_script_tag_carries_nonce():
    """
    Regression test for ENH-003.
    """
    response = client.get("/")
    assert response.status_code == 200

    import re
    csp = response.headers.get("Content-Security-Policy", "")
    nonce_match = re.search(r"'nonce-([a-f0-9]{32})'", csp)
    assert nonce_match is not None, f"No nonce in CSP header: {csp!r}"
    expected_nonce = nonce_match.group(1)

    html = response.text
    assert f'nonce="{expected_nonce}"' in html, (
        "index.html script tag does not carry the request-scoped nonce."
    )

def test_script_without_nonce_is_rejected_by_csp():
    """
    Verifies: the CSP header does not contain 'unsafe-inline' in script-src,
    """
    response = client.get("/health")
    csp = response.headers.get("Content-Security-Policy", "")

    script_src_section = csp.split("script-src")[1].split(";")[0] if "script-src" in csp else ""
    assert "'unsafe-inline'" not in script_src_section, (
        "ENH-003 regression: script-src still contains 'unsafe-inline'."
    )
