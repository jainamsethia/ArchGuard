"""ArchGuard dashboard FastAPI application."""

# ruff: noqa: E402 - load_dotenv() must run before any archguard import: several
# modules (archguard.config, _cookie_auth) read os.environ at import time, so
# hoisting these imports above it would silently ignore the operator's .env.

import asyncio
import importlib.metadata
import logging
import os
import secrets
import sys
import time
import uuid

from dotenv import load_dotenv

load_dotenv()

import re as _re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from archguard.dashboard._auth import _real_client_ip
from archguard.dashboard._rate_limit import rate_limiter
from archguard.observability.logger import configure_logging, correlation_id_var

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

def _installed_version() -> str:
    try:
        return importlib.metadata.version("archguard")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"

_startup_logger = logging.getLogger("archguard.startup")


async def _periodic_workspace_cleanup() -> None:
    """Remove stale workspaces every 15 minutes as defense-in-depth for crash scenarios."""
    import asyncio as _asyncio

    from archguard.dashboard.workspace import cleanup_stale_workspaces
    while True:
        await _asyncio.sleep(900)  # 15 minutes
        try:
            # Workspaces of jobs still held in memory are exempt: a user may be
            # reading results from one long after its clone finished.
            from archguard.dashboard.job_manager import job_manager
            active = {j.id for j in job_manager.list_jobs()}
            removed = await cleanup_stale_workspaces(
                max_age_seconds=900, active_job_ids=active
            )
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
    # Must be the first statement here. Until this ran, uvicorn configured only
    # its own loggers, so every archguard.* INFO record -- including the access
    # log below -- was dropped by a root logger sitting at WARNING with no
    # handler, and WARNING/ERROR reached stderr through logging.lastResort with
    # no timestamp, level or logger name. Nothing in this package logs at module
    # scope, so the lifespan is early enough to catch the whole process.
    configure_logging()
    _startup_logger.info("ArchGuard Dashboard starting up...")

    recommended = {
        "GEMINI_API_KEY": "L4 LLM explanations, the AI Advisor and AI fix suggestions will be disabled",
        "GITHUB_TOKEN": "GitHub API limited to 60 req/hr (unauthenticated)",
    }
    for var, consequence in recommended.items():
        if not os.environ.get(var):
            _startup_logger.warning("Optional env var %s not set - %s", var, consequence)

    if not os.environ.get("ARCHGUARD_DASHBOARD_TOKEN"):
        _startup_logger.warning(
            "ARCHGUARD_DASHBOARD_TOKEN is not set. "
            "Authentication relies on IP-based allowlisting (localhost only). "
            "Generate one with: python -c \"import secrets; "
            "print(secrets.token_hex(32))\""
        )

    proxy_ips = os.environ.get("ARCHGUARD_TRUSTED_PROXY_IPS", "").strip()
    if not proxy_ips:
        _startup_logger.warning(
            "ARCHGUARD_TRUSTED_PROXY_IPS is not set. If running behind a proxy "
            "(like Railway or Render), rate limiting will be broken because all "
            "users will share the proxy's IP. Set to '*' to trust all X-Forwarded-For "
            "headers, or a specific CIDR."
        )

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

    # State that is still process-local, named precisely. A blanket warning
    # that stayed constant while the facts changed under it is worse than none:
    # rate limits and the evolution cache now live in Redis when it is
    # configured, and saying otherwise trains operators to ignore this line.
    from archguard.redis_client import is_configured as _redis_configured

    still_local = ["analysis jobs", "login sessions"]
    if not _redis_configured():
        still_local += ["rate limits", "evolution cache"]
        _startup_logger.warning(
            "REDIS_URL is not set. %s are kept in this process only: they are "
            "lost on restart and not shared between instances.",
            ", ".join(still_local).capitalize(),
        )
    else:
        _startup_logger.warning(
            "%s are still held in this process and are lost on restart. "
            "Running more than one instance will not share them.",
            ", ".join(still_local).capitalize(),
        )

    _startup_logger.info("Dashboard ready.")
    yield
    if task:
        task.cancel()

    # Release the connection pools before the loop closes. Without this the
    # engine's sockets are torn down by garbage collection at interpreter exit,
    # which surfaces as "Event loop is closed" noise on every restart.
    from archguard.dashboard.job_manager import job_manager
    from archguard.db.session import dispose_engine
    from archguard.redis_client import close_redis

    cancelled = await job_manager.cancel_all_running(timeout=5.0)
    if cancelled:
        _startup_logger.warning(
            "Cancelled %d in-flight job(s) during shutdown; their /tmp workspaces "
            "will be swept by the next startup's stale-workspace cleanup.",
            cancelled,
        )

    try:
        await dispose_engine()
        close_redis()
    except Exception as exc:
        _startup_logger.warning("Error releasing datastore connections: %s", exc)

    _startup_logger.info("ArchGuard Dashboard shutting down.")

app = FastAPI(
    title="ArchGuard Dashboard",
    version=_installed_version(),
    lifespan=_lifespan,
)

_MAX_BODY = 1 * 1024 * 1024  # 1 MB - sufficient for all documented payloads


@app.middleware("http")
async def _limit_body_size(request: Request, call_next: Any) -> Any:
    """Reject requests whose Content-Length or actual body size exceeds 1 MB."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _MAX_BODY:
        return JSONResponse(
            status_code=413,
            content={"error": "Request body too large (max 1 MB)"},
        )

    # A lying or absent Content-Length must not get a free pass, so also cap at
    # read time. Stop feeding the body and flag it once the cap is crossed: the
    # route's own body parsing turns the truncated stream into its own error
    # (422/400), which would otherwise mask the real reason, so the flag is
    # re-checked after call_next and wins over whatever the route returned.
    receive = request._receive
    state = {"size": 0, "exceeded": False}

    async def wrapped_receive() -> Any:
        message = await receive()
        if message["type"] == "http.request":
            state["size"] += len(message.get("body", b""))
            if state["size"] > _MAX_BODY:
                state["exceeded"] = True
                return {"type": "http.disconnect"}
        return message

    request._receive = wrapped_receive

    _too_large = JSONResponse(
        status_code=413,
        content={"error": "Request body too large (max 1 MB)"},
    )
    try:
        response = await call_next(request)
    except Exception:
        if state["exceeded"]:
            return _too_large
        raise
    return _too_large if state["exceeded"] else response

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
    correlation_id = str(uuid.uuid4())[:8]
    # Bound before call_next so the downstream task inherits it: a task copies
    # the context at creation, which is how every record emitted while handling
    # this request -- not just the line below -- carries the same id.
    token = correlation_id_var.set(correlation_id)
    try:
        client_ip = _real_client_ip(request)
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000)
        _request_logger.info(
            "%s %s -> %d (%dms) ip=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            client_ip,
        )
        response.headers["X-Correlation-ID"] = correlation_id
        return response
    finally:
        # Reset rather than leave it bound: this coroutine runs in the server's
        # context, so an unreset value would be visible to whatever the worker
        # handles next.
        correlation_id_var.reset(token)

@app.middleware("http")
async def _deprecation_headers(request: Request, call_next: Any) -> Any:
    response = await call_next(request)
    if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/v1/"):
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = "Fri, 01 Jan 2027 00:00:00 GMT"
        response.headers["Link"] = f'</api/v1{request.url.path[4:]}>; rel="successor-version"'
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

    if os.environ.get("ENVIRONMENT") == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    return response

_exc_logger = logging.getLogger("archguard.exceptions")

@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # exc_info=exc, not .exception(): this handler runs outside the `except`
    # block that caught the error, so sys.exc_info() is not guaranteed to still
    # hold it -- and a 500 logged without its traceback is close to useless.
    _exc_logger.error(
        "Unhandled exception on %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
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
        # Workspace expired — raise 410 instead of silently falling back to cwd
        raise HTTPException(
            status_code=410,
            detail="Analysis workspace expired. Results are available from the stored run.",
        )
    return Path.cwd()

# Import routes AFTER app is defined to avoid circular dependencies
# API Versioning Policy (established 2026-06-25):
# All new routes MUST use the /api/v1/ prefix.
# Existing /api/ routes are maintained for backward compatibility.
# A future migration to /api/v1/ for all routes will include redirect aliases.
from fastapi.templating import Jinja2Templates

from archguard.dashboard.routes import (  # noqa: F401
    advisor,
    evolution,
    jobs,
    remediation,
    risk,
    runs,
    suppression,
)

_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

@app.get("/")
async def serve_index(request: Request) -> Response:
    return _templates.TemplateResponse(
        request,
        "index.html",
        {"csp_nonce": getattr(request.state, "csp_nonce", "")}
    )

@app.get("/dashboard.html")
async def serve_dashboard(request: Request) -> Response:
    return _templates.TemplateResponse(
        request,
        "dashboard.html",
        {"csp_nonce": getattr(request.state, "csp_nonce", "")}
    )

from archguard.dashboard._cookie_auth import COOKIE_NAME as _COOKIE_NAME
from archguard.dashboard._cookie_auth import issue_session, revoke_session


@app.post("/api/v1/auth/login", include_in_schema=False, dependencies=[Depends(rate_limiter)])
@app.post("/api/auth/login", include_in_schema=False, deprecated=True, dependencies=[Depends(rate_limiter)])
async def login(response: Response, token: str = Form(...)) -> dict[str, bool]:
    """Exchange the dashboard token for a session cookie."""
    import hmac as _hmac
    import os as _os
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


@app.post("/api/v1/auth/logout", include_in_schema=False, dependencies=[Depends(rate_limiter)])
@app.post("/api/auth/logout", include_in_schema=False, deprecated=True, dependencies=[Depends(rate_limiter)])
async def logout(request: Request, response: Response) -> dict[str, bool]:
    """Invalidate the current session cookie."""
    cookie_value = request.cookies.get(_COOKIE_NAME, "")
    if cookie_value:
        revoke_session(cookie_value)
    response.delete_cookie(key=_COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/v1/auth/status", include_in_schema=False, dependencies=[Depends(rate_limiter)])
@app.get("/api/auth/status", include_in_schema=False, deprecated=True, dependencies=[Depends(rate_limiter)])
async def auth_status(request: Request) -> dict[str, bool]:
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


# Catch-all for unrecognised /api/ paths — must be registered AFTER all real
# API routes but BEFORE the static-file mount so they return a proper 404
# instead of falling through to StaticFiles (which returns 405 for wrong
# methods on paths it matched).
@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def _api_404_catch_all() -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Not Found"})

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

__all__ = ["app"]
