"""Logging configuration for ArchGuard.

The web application is the primary consumer. Until it called this module (see
C1 in docs/BASELINE.md), `archguard.*` loggers propagated to a root logger left
at WARNING with no handler: every INFO record -- the entire HTTP access log --
was discarded, and WARNING/ERROR records reached stderr only through
``logging.lastResort``, which emits a bare message with no timestamp, level or
logger name.

Two deliberate constraints shape what follows.

*Never destructive.* Configuration is additive and idempotent: it installs one
handler it can recognise again later, and never clears ``root.handlers`` or
uses ``basicConfig(force=True)``. pytest's ``caplog`` captures through a root
handler, and three test modules assert on ``caplog.text`` for ``archguard.*``
loggers, so removing foreign handlers -- or setting ``propagate = False``
anywhere on that path -- would silently break them.

*Context via the record factory, not a filter.* A filter attached to a logger
runs only for records originating on that logger, so a filter on ``archguard``
would never see an ``archguard.http`` record. A filter attached to a handler
would tag the record, but only once that handler runs, so whether ``caplog``
observed the attribute would depend on handler ordering. ``setLogRecordFactory``
stamps the value at record creation, before any handler or filter, which is the
only placement that is deterministic for every consumer.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

#: Stamped on records emitted outside any HTTP request -- startup, shutdown,
#: background jobs, CLI runs. A placeholder rather than an empty string so the
#: column stays aligned in the console format and present in the JSON one.
NO_CORRELATION_ID = "-"

#: Set per request by the dashboard's logging middleware.
#:
#: Propagation is not uniform, and the difference decides whether a log line is
#: attributable to a request at all:
#:   asyncio.to_thread(...)              -> propagates (copies the context)
#:   loop.run_in_executor(None, ...)     -> does NOT propagate
#:   asyncio.create_task(...)            -> propagates (copies at creation)
#: The dashboard therefore offloads blocking work with ``asyncio.to_thread``,
#: and ``JobManager.run_job`` re-binds this to its own job id so a background
#: job's logs are not misattributed to whichever request happened to queue it.
correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "archguard_correlation_id", default=NO_CORRELATION_ID
)

_HANDLER_MARKER = "_archguard_owned_handler"


def owns_handler(handler: logging.Handler) -> bool:
    """True if *handler* is one this module installed.

    Lets configuration replace its own handler without touching anyone else's.
    """
    return getattr(handler, _HANDLER_MARKER, False) is True


class _CorrelationRecordFactory:
    """Wraps the previous record factory and stamps ``correlation_id``.

    A class rather than a closure so installation is idempotent by
    construction: ``isinstance`` tells us ours is already in place, without a
    module-level flag. Chaining a fresh wrapper on every call would otherwise
    nest one layer per call and re-read the context var that many times per
    record.
    """

    def __init__(self, base: Callable[..., logging.LogRecord]) -> None:
        self._base = base

    def __call__(self, *args: Any, **kwargs: Any) -> logging.LogRecord:
        record = self._base(*args, **kwargs)
        # Written through __dict__, the same channel logging itself uses for
        # `extra=`. Note the consequence: because every record now arrives
        # already carrying this key, `logger.info(..., extra={"correlation_id":
        # ...})` raises KeyError("Attempt to overwrite 'correlation_id' in
        # LogRecord") from Logger.makeRecord. Nothing in this package passes
        # `extra=`, and overriding the id of the request you are inside is not
        # a thing callers should be doing -- but set the context var if you
        # genuinely need a different value. The guard below is for records that
        # reached us already stamped, e.g. re-emitted through another factory.
        if "correlation_id" not in record.__dict__:
            record.__dict__["correlation_id"] = correlation_id_var.get()
        return record


def _install_record_factory() -> None:
    """Stamp ``correlation_id`` onto every record at creation."""
    current = logging.getLogRecordFactory()
    if isinstance(current, _CorrelationRecordFactory):
        return
    logging.setLogRecordFactory(_CorrelationRecordFactory(current))


class StructuredFormatter(logging.Formatter):
    """One JSON object per line, for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            # getattr, not record.correlation_id: a record can be constructed
            # directly by a third-party library, or before the factory was
            # installed, and a formatter that raises loses the log line.
            "correlation_id": getattr(record, "correlation_id", NO_CORRELATION_ID),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


class ConsoleFormatter(logging.Formatter):
    """Human-readable single line, for local development."""

    DEFAULT_FMT = "%(asctime)s %(levelname)-8s %(name)s [%(correlation_id)s] %(message)s"

    def __init__(self) -> None:
        super().__init__(fmt=self.DEFAULT_FMT, datefmt="%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        # The format string references correlation_id, so a record that never
        # passed through the factory would raise KeyError mid-emit.
        if "correlation_id" not in record.__dict__:
            record.__dict__["correlation_id"] = NO_CORRELATION_ID
        return super().format(record)


def _resolve_level(verbose: bool, quiet: bool, level: int | str | None) -> int:
    if level is not None:
        resolved = logging.getLevelName(level) if isinstance(level, str) else level
        return resolved if isinstance(resolved, int) else logging.INFO
    if quiet:
        return logging.ERROR
    if verbose:
        return logging.DEBUG
    from_env = os.environ.get("LOG_LEVEL", "").strip().upper()
    if from_env:
        named = logging.getLevelName(from_env)
        if isinstance(named, int):
            return named
        logging.getLogger(__name__).warning(
            "LOG_LEVEL=%r is not a recognised level name - using INFO", from_env
        )
    return logging.INFO


def _use_json(json_logs: bool | None) -> bool:
    if json_logs is not None:
        return json_logs
    if os.environ.get("ENVIRONMENT", "").strip().lower() == "production":
        return True
    # A non-TTY stderr means the output is being collected by something -- a
    # container runtime, CI, a log shipper -- rather than read by a person.
    return not sys.stderr.isatty()


def configure_logging(
    verbose: bool = False,
    quiet: bool = False,
    json_logs: bool | None = None,
    level: int | str | None = None,
) -> None:
    """Install ArchGuard's log handler, formatter and correlation-id factory.

    Safe to call more than once and safe to call alongside other logging
    configuration: it replaces only the handler it installed itself.

    Args:
        verbose: DEBUG level. Used by the CLI's ``--verbose`` flag.
        quiet: ERROR level, and wins over *verbose*. Used by the CLI's
            ``--quiet`` flag.
        json_logs: force JSON on or off. When None, JSON is used in production
            or whenever stderr is not a terminal.
        level: explicit level, overriding *verbose* / *quiet* / ``LOG_LEVEL``.
    """
    resolved_level = _resolve_level(verbose, quiet, level)

    _install_record_factory()

    root = logging.getLogger()

    # Replace only our own previous handler. Never basicConfig(force=True) and
    # never clear root.handlers: both remove *and close* pytest's
    # LogCaptureHandler, which silences caplog for the remainder of that test.
    for stale in [h for h in root.handlers if owns_handler(h)]:
        root.removeHandler(stale)
        stale.close()

    handler = logging.StreamHandler(sys.stderr)
    setattr(handler, _HANDLER_MARKER, True)
    handler.setFormatter(StructuredFormatter() if _use_json(json_logs) else ConsoleFormatter())
    root.addHandler(handler)

    # The level goes on the "archguard" logger, not on root. Root stays at its
    # default WARNING so a dependency's INFO chatter does not become our log
    # volume, while our own INFO records still reach the root handler: a
    # logger's level gates only where the record originates, not during
    # propagation.
    #
    # `propagate` is deliberately left True. The handler lives on root, so our
    # records reach it by propagating there -- and the same propagation is what
    # lets caplog observe them. Setting propagate=False here would both orphan
    # the records and break tests/unit/test_audit_logger.py,
    # test_coupling.py and test_gemini_client.py.
    logging.getLogger("archguard").setLevel(resolved_level)

    # uvicorn.access is superseded by the dashboard's own access log, which
    # additionally carries the correlation id, the request duration and the
    # proxy-aware client IP. Leaving both on logs every request twice, in two
    # formats, on two streams -- uvicorn.access writes to stdout, everything
    # else here to stderr. WARNING rather than CRITICAL so a genuine failure
    # inside uvicorn's access path still surfaces.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
