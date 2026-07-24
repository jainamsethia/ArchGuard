"""Regression guard for the hardened Content-Security-Policy header.

Phase 1 of the production-readiness pass removed every inline event handler
(``onclick=`` etc.) from the dashboard so the UI works under a strict
``script-src 'self' 'nonce-...'`` policy. This test fails if the CSP is ever
weakened to allow ``'unsafe-inline'`` in ``script-src`` (which would silently
re-enable the dead-click class of bugs the inline handlers caused).
"""
from fastapi.testclient import TestClient

from archguard.dashboard.app import app


def test_script_src_has_no_unsafe_inline() -> None:
    client = TestClient(app)
    resp = client.get("/health")
    csp = resp.headers.get("content-security-policy", "")
    assert "script-src" in csp, "CSP must define a script-src directive"

    script_section = csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "'unsafe-inline'" not in script_section, (
        "script-src must not allow 'unsafe-inline' — inline handlers were "
        "removed from the dashboard; re-enabling would resurrect dead-click bugs."
    )


def test_csp_contains_nonce() -> None:
    """CSP script-src must carry a per-request nonce (ENH-003)."""
    client = TestClient(app)
    resp = client.get("/health")
    csp = resp.headers.get("content-security-policy", "")
    assert "nonce-" in csp, "CSP must include a per-request nonce in script-src"
