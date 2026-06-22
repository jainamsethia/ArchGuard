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


def _installed_version() -> str:
    try:
        return importlib.metadata.version("archguard")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


app = FastAPI(title="ArchGuard Dashboard", version=_installed_version())

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
