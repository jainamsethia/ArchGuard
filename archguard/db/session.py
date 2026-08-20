"""Async engine and session management.

One engine per process, created lazily so importing this module does not open
sockets -- tests, Alembic and the worker all import it without necessarily
wanting a live pool.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)

#: Set to a URL with a driver, e.g.
#: ``postgresql+asyncpg://user:pass@localhost:5432/archguard_dev``.
DATABASE_URL_ENV = "DATABASE_URL"

class _EngineState:
    """Holds the process-wide engine.

    An object with mutable attributes rather than two module-level names, so
    creating and disposing the engine does not need `global` -- which ruff
    flags, and which makes the lifecycle harder to follow than it needs to be.
    """

    engine: AsyncEngine | None = None
    sessionmaker: async_sessionmaker[AsyncSession] | None = None


_state = _EngineState()


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when a database operation is attempted with no DATABASE_URL."""


def database_url() -> str:
    url = os.environ.get(DATABASE_URL_ENV, "").strip()
    if not url:
        raise DatabaseNotConfiguredError(
            f"{DATABASE_URL_ENV} is not set. ArchGuard stores users, jobs, runs "
            "and suppressions in PostgreSQL; there is no file-based fallback."
        )
    return url


def _normalise(url: str) -> str:
    """Accept the driverless form platforms hand out.

    Render and Railway inject ``postgres://`` or ``postgresql://``. SQLAlchemy
    needs the driver named explicitly, and silently picking psycopg2 in an async
    application deadlocks rather than erroring, so map it here instead.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


def get_engine() -> AsyncEngine:
    if _state.engine is None:
        url = _normalise(database_url())
        _state.engine = create_async_engine(
            url,
            # pre_ping costs one round trip per checkout and buys immunity to
            # the connection a managed Postgres closed while the pool was idle,
            # which otherwise surfaces as a random 500 after quiet periods.
            pool_pre_ping=True,
            pool_size=int(os.environ.get("ARCHGUARD_DB_POOL_SIZE", "5")),
            max_overflow=int(os.environ.get("ARCHGUARD_DB_MAX_OVERFLOW", "10")),
            echo=os.environ.get("ARCHGUARD_DB_ECHO", "").lower() in ("1", "true"),
        )
        logger.info("Database engine created for %s", _redact(url))
    return _state.engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _state.sessionmaker is None:
        _state.sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _state.sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """A transactional session: commits on success, rolls back on error."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Use with ``Depends(get_session)``."""
    async with session_scope() as session:
        yield session


async def dispose_engine() -> None:
    """Close the pool. Called on application shutdown."""
    if _state.engine is not None:
        await _state.engine.dispose()
        logger.info("Database engine disposed")
    _state.engine = None
    _state.sessionmaker = None


def _redact(url: str) -> str:
    """Strip the password before a URL reaches a log line."""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"
