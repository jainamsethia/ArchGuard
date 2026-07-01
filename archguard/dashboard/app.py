"""ArchGuard dashboard FastAPI application."""

import sys
import asyncio
import os
import logging
import time
import importlib.metadata
import secrets
from pathlib import Path
from typing import Any, Annotated
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Query, HTTPException, Form, Response
import re as _re
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from archguard.config import AUDIT_LOG_FILENAME

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

def _installed_version() -> str:
    try:
        return importlib.metadata.version("archguard")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"

async def _periodic_workspace_cleanup() -> None:
    """Remove stale workspaces every 15 minutes as defense-in-depth for crash scenarios."""
    import asyncio as _asyncio
    from archguard.dashboard.workspace import cleanup_stale_workspaces
    while True:
        await _asyncio.sleep(900)  # 15 minutes
        try:
            removed = await cleanup_stale_workspaces(max_age_seconds=900)
            if removed:
                _startup_logger.info(
                    "Periodic cleanup: removed %d stale workspace(s)", removed
                )
        except Exception as exc:
            _startup_logger.warning(
                "Periodic workspace cleanup error (non-fatal): %s", exc
            )


@asynccontextmanager
async def _lifespan(app_instance: FastAPI) -> Any:
    _startup_logger = logging.getLogger("archguard.startup")
    _startup_logger.info("ArchGuard Dashboard starting up...")

    recommended = {
        "ANTHROPIC_API_KEY": "L4 LLM explanations will be skipped",
        "GITHUB_TOKEN": "GitHub API limited to 60 req/hr (unauthenticated)",
    }
    for var, consequence in recommended.items():
        if not os.environ.get(var):
            _startup_logger.warning("Optional env var %s not set - %s", var, consequence)

    task = None
    try:
        from archguard.dashboard.workspace import cleanup_stale_workspaces
        removed = await cleanup_stale_workspaces(max_age_seconds=3600)
        if removed:
            _startup_logger.info("Removed %d stale workspace(s) on startup", removed)
        import asyncio as _asyncio
        task = _asyncio.create_task(_periodic_workspace_cleanup())
    except Exception as exc:
        _startup_logger.warning("Startup workspace cleanup failed (non fatal): %s", exc)

    # Multi-instance guard - document the in-memory state constraint at startup
    _startup_logger.warning(
        "ArchGuard dashboard uses in-memory job/session/rate-limit state. "
        "Multi-instance deployments (load balancers, autoscaling) will lose "
        "state across instances. Enable Redis (MOD-001) for multi-instance support."
    )

    _startup_logger.info("Dashboard ready.")
    yield
    if task:
        task.cancel()
    _startup_logger.info("ArchGuard Dashboard shutting down.")

app = FastAPI(
    title="ArchGuard Dashboard",
    version=_installed_version(),
    lifespan=_lifespan,
)

_MAX_BODY = 1 * 1024 * 1024  # 1 MB - sufficient for all documented payloads


@app.middleware("http")
async def _limit_body_size(request: Request, call_next: Any) -> Any:
    """Reject requests whose Content-Length exceeds 1 MB before parsing."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _MAX_BODY:
        return JSONResponse(
            status_code=413,
            content={"error": "Request body too large (max 1 MB)"},
        )
    return await call_next(request)

# Reusable validated job_id type for all route query parameters.
# UUIDs are 36 chars; allow up to 64 for flexibility. Only hex + hyphens.
JobIdQuery = Annotated[
    str | None,
    Query(pattern=r"^[a-f0-9\-]{36,64}$", max_length=64)
]

_allowed_origins: list[str] = [
    o.strip()
    for o in os.environ.get(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["Content-Type"],
)

_request_logger = logging.getLogger("archguard.http")

@app.middleware("http")
async def _log_requests(request: Request, call_next: Any) -> Any:
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = round((time.monotonic() - start) * 1000)
    _request_logger.info(
        "%s %s -> %d (%dms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response

@app.middleware("http")
async def _security_headers(request: Request, call_next: Any) -> Any:
    """Attach security headers to every response, including a per-request CSP nonce."""
    nonce = secrets.token_hex(16)
    request.state.csp_nonce = nonce

    response = await call_next(request)

    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "connect-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self';"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

_exc_logger = logging.getLogger("archguard.exceptions")

@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    _exc_logger.exception(
        "Unhandled exception on %s %s", request.method, request.url.path
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "type": type(exc).__name__,
        },
    )

_APP_START_TIME = time.time()
STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"

def get_target_path(job_id: str | None = None) -> Path:
    if job_id:
        import tempfile
        # Double-check even after query validation - defense in depth
        if not _re.fullmatch(r"[a-f0-9\-]{36,64}", job_id):
            raise HTTPException(status_code=400, detail="Invalid job_id format")
        tmp = Path(tempfile.gettempdir())
        path = (tmp / f"archguard-{job_id}" / "repo").resolve()
        # Ensure the resolved path is strictly inside /tmp/archguard-<uuid>/
        expected_prefix = (tmp / f"archguard-{job_id}").resolve()
        if not str(path).startswith(str(expected_prefix)):
            raise HTTPException(status_code=400, detail="Invalid job_id")
        if path.exists():
            return path
    return Path.cwd()

def get_audit_path(job_id: str | None = None) -> Path:
    target = get_target_path(job_id)
    audit_file = target / AUDIT_LOG_FILENAME
    if job_id and not audit_file.exists():
        return Path.cwd() / AUDIT_LOG_FILENAME
    return audit_file

# Import routes AFTER app is defined to avoid circular dependencies
# API Versioning Policy (established 2026-06-25):
# All new routes MUST use the /api/v1/ prefix.
# Existing /api/ routes are maintained for backward compatibility.
# A future migration to /api/v1/ for all routes will include redirect aliases.
from archguard.dashboard.routes import advisor, evolution, jobs, remediation, runs  # noqa: E402, F401

from fastapi.templating import Jinja2Templates

_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

@app.get("/")
async def serve_index(request: Request):
    return _templates.TemplateResponse(
        request,
        "index.html",
        {"csp_nonce": getattr(request.state, "csp_nonce", "")}
    )

@app.get("/dashboard.html")
async def serve_dashboard(request: Request):
    return _templates.TemplateResponse(
        request,
        "dashboard.html",
        {"csp_nonce": getattr(request.state, "csp_nonce", "")}
    )

from archguard.dashboard._cookie_auth import issue_session, revoke_session, COOKIE_NAME as _COOKIE_NAME


@app.post("/api/auth/login", include_in_schema=False)
async def login(response: Response, token: str = Form(...)) -> dict:
    """Exchange the dashboard token for a session cookie."""
    import os as _os, hmac as _hmac
    stored = _os.environ.get("ARCHGUARD_DASHBOARD_TOKEN", "")
    if not stored or not _hmac.compare_digest(token, stored):
        raise HTTPException(status_code=401, detail="Invalid token")
    cookie_value = issue_session(stored)
    response.set_cookie(
        key=_COOKIE_NAME,
        value=cookie_value,
        httponly=True,
        samesite="strict",
        secure=_os.environ.get("ENVIRONMENT", "") == "production",
        max_age=int(_os.environ.get("ARCHGUARD_SESSION_COOKIE_TTL", "86400")),
        path="/",
    )
    return {"ok": True}


@app.post("/api/auth/logout", include_in_schema=False)
async def logout(request: Request, response: Response) -> dict:
    """Invalidate the current session cookie."""
    cookie_value = request.cookies.get(_COOKIE_NAME, "")
    if cookie_value:
        revoke_session(cookie_value)
    response.delete_cookie(key=_COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/auth/status", include_in_schema=False)
async def auth_status(request: Request) -> dict:
    """Return whether auth is required and whether the current request is authenticated.

    This endpoint intentionally bypasses check_token so it is always reachable.
    """
    import os as _os
    from archguard.dashboard._cookie_auth import validate_session_cookie
    stored = _os.environ.get("ARCHGUARD_DASHBOARD_TOKEN", "")
    token_required = bool(stored)
    if not token_required:
        return {"token_required": False, "authenticated": True}
    cookie = request.cookies.get(_COOKIE_NAME, "")
    authenticated = bool(cookie and validate_session_cookie(cookie, stored))
    return {"token_required": token_required, "authenticated": authenticated}


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

__all__ = ["app"]
