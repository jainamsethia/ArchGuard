"""How the route table is shaped, and why the shape matters.

Replaces ``test_app_versioning.py``, which pinned the dual-URL scheme this
removes. Two of its four assertions were checking Deprecation headers on
``/api/health`` and ``/api/v1/health`` -- paths that never existed as routes,
so it was asserting middleware behaviour on a 404.
"""

from __future__ import annotations

from starlette.routing import Mount

from archguard.dashboard.app import app

#: Endpoints that are deliberately not under /api/v1.
UNPREFIXED = {
    "/",
    "/dashboard.html",
    # Platform health checks are unauthenticated by definition, and burying
    # this under /api/v1 would make liveness depend on the database.
    "/health",
    # Registered with GitHub as the OAuth callback.
    "/auth/github",
    "/auth/github/callback",
    # FastAPI's own.
    "/docs",
    "/docs/oauth2-redirect",
    "/openapi.json",
    "/redoc",
    # The catch-all that turns an unknown /api path into a JSON 404 rather
    # than letting it fall through to StaticFiles.
    "/api/{path:path}",
}


def _paths() -> set[str]:
    return {
        r.path for r in app.routes if getattr(r, "path", None) and not isinstance(r, Mount)
    }


def test_every_api_route_is_under_v1():
    """The dual-URL scheme is gone.

    Around twenty endpoints carried a duplicated ``deprecated=True`` alias for
    an API with no external consumers -- and applied it inconsistently, so
    /deps, /risk, /suppressions and /advisor/ask never had one. Every alias was
    a second path that per-user scoping had to get right independently.
    """
    stray = {
        p
        for p in _paths()
        if p.startswith("/api/") and not p.startswith("/api/v1/") and p not in UNPREFIXED
    }
    assert not stray, f"unversioned API routes: {sorted(stray)}"


def test_nothing_outside_the_allowlist_is_unprefixed():
    """A new endpoint gets /api/v1 unless someone decides otherwise here."""
    stray = {p for p in _paths() if not p.startswith("/api/v1/") and p not in UNPREFIXED}
    assert not stray, f"unexpected unprefixed routes: {sorted(stray)}"


def test_the_static_mount_is_last():
    """StaticFiles at "/" matches everything, so anything after it is shadowed.

    This used to depend on import order: route modules decorated the shared app
    object at import time, so a submodule imported before app.py finished
    registered its routes *after* this mount and became a static 404. There was
    an 18-line comment in routes/__init__.py holding that off. Routers are
    values, so app.py decides the order -- but the mount still has to be last.
    """
    mounts = [i for i, r in enumerate(app.routes) if isinstance(r, Mount)]
    assert mounts, "the static mount is missing"
    assert mounts[-1] == len(app.routes) - 1, (
        "a route is registered after the static mount and will never match"
    )


def test_the_api_catch_all_precedes_the_mount():
    """Otherwise an unknown /api path gets a 405 from StaticFiles, not a 404."""
    positions = {
        getattr(r, "path", None): i for i, r in enumerate(app.routes)
    }
    mount_at = max(i for i, r in enumerate(app.routes) if isinstance(r, Mount))
    assert positions["/api/{path:path}"] < mount_at


def test_no_route_is_registered_twice():
    """One path per method. Duplicates shadow each other silently."""
    seen: dict[tuple[str, str], int] = {}
    for r in app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None) or set()
        if not path or isinstance(r, Mount):
            continue
        for method in methods:
            seen[(path, method)] = seen.get((path, method), 0) + 1
    duplicates = {k: v for k, v in seen.items() if v > 1}
    assert not duplicates, f"duplicate registrations: {duplicates}"


def test_the_deprecation_headers_are_gone():
    """The middleware described a scheme that no longer exists."""
    import archguard.dashboard.app as app_module

    source = app_module.__file__
    assert source
    with open(source, encoding="utf-8") as fh:
        text = fh.read()
    assert "_deprecation_headers" not in text
    assert "successor-version" not in text
