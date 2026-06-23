"""Shared FastAPI app instance, auth, and rate-limiting infrastructure for the
ArchGuard dashboard. Route modules under archguard/dashboard/routes/ import
`app` from here and register their endpoints on it; nothing in this file should
import from any routes/*.py module (one-directional dependency, to keep the
route modules independently readable)."""

import importlib.metadata
import os
import logging
import time
import threading
from cachetools import TTLCache as RateLimitCache
from collections import deque
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Any
from pathlib import Path
from archguard.config import AUDIT_LOG_FILENAME
from archguard.llm.advisor import ArchitectureAdvisor
from archguard.llm.openai_provider import OpenAIAdvisorProvider
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


def _installed_version() -> str:
    try:
        return importlib.metadata.version("archguard")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


@asynccontextmanager
async def _lifespan(app_instance: FastAPI) -> Any:
    """Application lifespan handler.
    Startup:
    - Validates that no REQUIRED env vars are missing (none currently required)
    - Warns about missing RECOMMENDED vars (ANTHROPIC_API_KEY, GITHUB_TOKEN)
    - Cleans up stale temp workspaces from previous crashed runs
    Shutdown:
    - No cleanup needed (connections are stateless)
    """
    _startup_logger = logging.getLogger("archguard.startup")
    _startup_logger.info("ArchGuard Dashboard starting up…")

    # Warn (do not crash) if recommended vars are missing
    recommended = {
        "ANTHROPIC_API_KEY": "L4 LLM explanations will be skipped",
        "GITHUB_TOKEN": "GitHub API limited to 60 req/hr (unauthenticated)",
    }
    for var, consequence in recommended.items():
        if not os.environ.get(var):
            _startup_logger.warning("Optional env var %s not set — %s", var, consequence)

    # Clean up stale workspaces from previous crashed runs
    try:
        from archguard.dashboard.workspace import cleanup_stale_workspaces
        removed = await cleanup_stale_workspaces(max_age_seconds=3600)
        if removed:
            _startup_logger.info("Removed %d stale workspace(s) on startup", removed)
    except Exception as exc:
        _startup_logger.warning("Startup workspace cleanup failed (non fatal): %s", exc)

    _startup_logger.info("Dashboard ready.")
    yield  # ← application runs here
    _startup_logger.info("ArchGuard Dashboard shutting down.")

app = FastAPI(
    title="ArchGuard Dashboard",
    version=_installed_version(),
    lifespan=_lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────
# Allow the configured origins to call the API from a browser.
# In production, set ALLOWED_ORIGINS to your frontend domain:
#   ALLOWED_ORIGINS=https://your-app.vercel.app
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
    allow_headers=["*"],
    expose_headers=["Content-Type"],
)
# ── /CORS ─────────────────────────────────────────────────────────────────

# ── Request logging middleware ────────────────────────────────────────────

_request_logger = logging.getLogger("archguard.http")

@app.middleware("http")
async def _log_requests(request: Request, call_next: Any) -> Any:
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = round((time.monotonic() - start) * 1000)
    _request_logger.info(
        "%s %s → %d (%dms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response

# ── Global exception handler ──────────────────────────────────────────────

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

# Track startup time for /health uptime
_APP_START_TIME = time.time()

STATIC_DIR = Path(__file__).parent / "static"

security = HTTPBearer(auto_error=False)

RATE_LIMIT_WINDOW = 60.0
RATE_LIMIT_MAX_REQUESTS = 50

_RATE_LOCK = threading.Lock()
# In-memory cache with maxsize=10_000 evicts the oldest entry when full, providing OOM protection
RATE_LIMITS: RateLimitCache[str, deque[float]] = RateLimitCache(
    maxsize=10_000, ttl=RATE_LIMIT_WINDOW * 2
)

_LLM_MAX = 10
_LLM_LIMITS: RateLimitCache[str, deque[float]] = RateLimitCache(
    maxsize=10_000, ttl=RATE_LIMIT_WINDOW * 2
)
_LLM_RATE_LOCK = threading.Lock()


def rate_limiter(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    with _RATE_LOCK:
        if client_ip not in RATE_LIMITS:
            RATE_LIMITS[client_ip] = deque()

        history = RATE_LIMITS[client_ip]

        while history and history[0] < now - RATE_LIMIT_WINDOW:
            history.popleft()

        if len(history) >= RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests",
            )

        history.append(now)


def _llm_rate_limit(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    with _LLM_RATE_LOCK:
        if client_ip not in _LLM_LIMITS:
            _LLM_LIMITS[client_ip] = deque()

        history = _LLM_LIMITS[client_ip]

        while history and history[0] < now - RATE_LIMIT_WINDOW:
            history.popleft()

        if len(history) >= _LLM_MAX:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many LLM requests",
            )

        history.append(now)


def check_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> None:
    token = os.environ.get("ARCHGUARD_DASHBOARD_TOKEN")
    if token:
        if not credentials or credentials.credentials != token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing token",
                headers={"WWW-Authenticate": "Bearer"},
            )
    else:
        client_host = request.client.host if request.client else "unknown"
        if client_host not in (
            "127.0.0.1",
            "localhost",
            "::1",
            "testclient",
            "testserver",
        ):
            allow_remote = os.environ.get(
                "ARCHGUARD_DASHBOARD_ALLOW_REMOTE", ""
            ).lower() in ("1", "true")
            if not allow_remote:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Dashboard requires ARCHGUARD_DASHBOARD_TOKEN to be set for remote access",
                )
            else:
                logging.warning(
                    f"Dashboard accessed from {client_host} without token authentication! Consider setting ARCHGUARD_DASHBOARD_TOKEN."
                )


def get_audit_path() -> Path:
    return Path.cwd() / AUDIT_LOG_FILENAME


# ─────────────────────────────────────────────────────────────────────────────
# In-memory session store  (session_id → session dict)
# ─────────────────────────────────────────────────────────────────────────────

_SESSION_LOCK = threading.Lock()
SESSION_STORE: dict[str, dict[str, Any]] = {}
SESSION_TTL_SECONDS = int(
    os.environ.get("ARCHGUARD_SESSION_TTL", "3600")
)  # 1 h default


def _purge_expired_sessions() -> None:
    """Remove sessions older than SESSION_TTL_SECONDS. Called opportunistically."""
    now = time.time()
    with _SESSION_LOCK:
        expired = [
            k for k, v in SESSION_STORE.items() if now - v["_ts"] > SESSION_TTL_SECONDS
        ]
        for k in expired:
            del SESSION_STORE[k]


def _build_advisor() -> ArchitectureAdvisor:
    """Construct an ArchitectureAdvisor using the configured provider.

    Uses OpenAIAdvisorProvider for the session-based analysis endpoint (initial
    recommendations). The streaming chat endpoint /api/v1/advisor/ask uses
    ArchitectureAdvisor.ask_stream() directly via the Anthropic SDK.
    """
    provider = OpenAIAdvisorProvider()
    return ArchitectureAdvisor(provider)
