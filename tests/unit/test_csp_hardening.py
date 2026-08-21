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


def test_the_four_directives_with_no_default_src_fallback_are_present() -> None:
    """D8.

    ``default-src`` does not stand in for these. ``frame-ancestors`` and
    ``form-action`` have no fallback at all, so without them the page can be
    framed for clickjacking and a form on it can post to any origin;
    ``object-src`` and ``base-uri`` fall back only in modern browsers, and a
    ``<base>`` tag injection rewrites every relative URL on the page.
    """
    csp = TestClient(app).get("/health").headers.get("content-security-policy", "")
    for directive in (
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ):
        assert directive in csp, f"CSP is missing {directive!r}"


def test_avatars_are_the_only_third_party_images_allowed() -> None:
    """GitHub avatars are the one remote asset the signed-in UI renders."""
    csp = TestClient(app).get("/health").headers.get("content-security-policy", "")
    img_section = csp.split("img-src", 1)[1].split(";", 1)[0]
    assert "avatars.githubusercontent.com" in img_section
    assert "*" not in img_section, "img-src must not be a wildcard"
