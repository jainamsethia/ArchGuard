"""ArchGuard dashboard FastAPI application."""

import sys
import asyncio
import os
import logging
import time
import importlib.metadata
from pathlib import Path
from typing import Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
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

    try:
        from archguard.dashboard.workspace import cleanup_stale_workspaces
        removed = await cleanup_stale_workspaces(max_age_seconds=3600)
        if removed:
            _startup_logger.info("Removed %d stale workspace(s) on startup", removed)
    except Exception as exc:
        _startup_logger.warning("Startup workspace cleanup failed (non fatal): %s", exc)

    _startup_logger.info("Dashboard ready.")
    yield
    _startup_logger.info("ArchGuard Dashboard shutting down.")

app = FastAPI(
    title="ArchGuard Dashboard",
    version=_installed_version(),
    lifespan=_lifespan,
)

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

def get_target_path(job_id: str | None = None) -> Path:
    if job_id:
        import tempfile
        path = Path(tempfile.gettempdir()) / f"archguard-{job_id}" / "repo"
        if path.exists():
            return path
    return Path.cwd()

def get_audit_path(job_id: str | None = None) -> Path:
    return get_target_path(job_id) / AUDIT_LOG_FILENAME

# Import routes AFTER app is defined to avoid circular dependencies
from archguard.dashboard.routes import advisor, evolution, jobs, remediation, runs  # noqa: E402, F401

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

__all__ = ["app"]
