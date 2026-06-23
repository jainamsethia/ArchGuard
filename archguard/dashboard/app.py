"""ArchGuard dashboard FastAPI application. Route modules self-register onto
the shared `app` instance from archguard.dashboard._state when imported."""

import sys
import asyncio
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi.staticfiles import StaticFiles
from archguard.dashboard._state import app, STATIC_DIR
from archguard.dashboard.routes import advisor, evolution, jobs, remediation, runs  # noqa: F401

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

__all__ = ["app"]
