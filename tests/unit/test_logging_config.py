"""Unit tests for archguard.observability.logger and the web app's logging setup.

The regression these guard against (C1): the web application never called
configure_logging(), so under uvicorn every ``archguard.*`` INFO record -- the
entire HTTP access log included -- was discarded by a root logger left at
WARNING with no handler, and WARNING/ERROR records reached stderr only through
``logging.lastResort``, without a timestamp, level name or logger name.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from archguard.observability.logger import (
    NO_CORRELATION_ID,
    ConsoleFormatter,
    StructuredFormatter,
    configure_logging,
    correlation_id_var,
    owns_handler,
)


@pytest.fixture(autouse=True)
def _restore_logging_state() -> Iterator[None]:
    """Stop this module's global logging mutations from leaking into other tests.

    configure_logging() sets levels on the shared "archguard" and
    "uvicorn.access" loggers and installs a root handler and a record factory.
    Left in place, the quiet=True call below would raise the archguard logger to
    ERROR and silence the caplog assertions in test_audit_logger.py,
    test_coupling.py and test_gemini_client.py -- which would otherwise survive
    only by the accident that those filenames sort before this one.
    """
    root = logging.getLogger()
    saved = (
        list(root.handlers),
        root.level,
        logging.getLogger("archguard").level,
        logging.getLogger("uvicorn.access").level,
        logging.getLogRecordFactory(),
    )
    try:
        yield
    finally:
        handlers, root_level, archguard_level, access_level, factory = saved
        # Rebind the list rather than close anything: pytest's own
        # LogCaptureHandler is in here.
        root.handlers[:] = handlers
        root.setLevel(root_level)
        logging.getLogger("archguard").setLevel(archguard_level)
        logging.getLogger("uvicorn.access").setLevel(access_level)
        logging.setLogRecordFactory(factory)


def _our_handler() -> logging.Handler:
    installed = [h for h in logging.getLogger().handlers if owns_handler(h)]
    assert len(installed) == 1, f"expected exactly one archguard handler, got {len(installed)}"
    return installed[0]


class _Capture(logging.Handler):
    """Collects records without filtering or formatting them."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def capture_root() -> Iterator[_Capture]:
    """Attach a capture handler to the root logger for the duration of a test.

    Deliberately does NOT change any logger's level: these tests must observe
    the levels the application itself configures, otherwise they would pass
    against the unconfigured state that C1 describes.
    """
    handler = _Capture()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)


@pytest.fixture
def web_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A TestClient that actually runs the ASGI lifespan.

    Every other test in this suite constructs ``TestClient(app)`` bare, which
    never starts the lifespan -- which is why the startup path had no coverage
    and C1 went unnoticed.

    The real lifespan sweeps ``/tmp/archguard-*`` for workspaces older than an
    hour. That is correct in production and destructive in a test run on a
    developer machine, so the sweep is neutralised here; it is not what these
    tests are about.
    """
    from archguard.dashboard import workspace

    async def _no_sweep(*_args: object, **_kwargs: object) -> int:
        return 0

    monkeypatch.setattr(workspace, "cleanup_stale_workspaces", _no_sweep)

    from archguard.dashboard.app import app

    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# StructuredFormatter
# ---------------------------------------------------------------------------


def _record(**overrides: object) -> logging.LogRecord:
    kwargs: dict[str, object] = {
        "name": "archguard.test",
        "level": logging.INFO,
        "pathname": __file__,
        "lineno": 1,
        "msg": "hello %s",
        "args": ("world",),
        "exc_info": None,
    }
    kwargs.update(overrides)
    return logging.LogRecord(**kwargs)  # type: ignore[arg-type]


def test_structured_formatter_produces_valid_json() -> None:
    parsed = json.loads(StructuredFormatter().format(_record()))
    assert parsed["message"] == "hello world"
    assert parsed["level"] == "INFO"


def test_structured_formatter_emits_the_correlation_id() -> None:
    record = _record()
    record.correlation_id = "abc12345"
    parsed = json.loads(StructuredFormatter().format(record))
    assert parsed["correlation_id"] == "abc12345"


def test_structured_formatter_tolerates_a_record_with_no_correlation_id() -> None:
    """Records created before any configuration -- or by a third-party library
    that bypasses the record factory -- must not make the formatter raise."""
    parsed = json.loads(StructuredFormatter().format(_record()))
    assert parsed["correlation_id"] == NO_CORRELATION_ID


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


def test_configure_logging_accepts_the_cli_signature() -> None:
    """archguard/cli/main.py calls configure_logging(verbose=..., quiet=...).

    The CLI is slated for removal, but it still exists and must keep working
    until the task that deletes it.
    """
    configure_logging(verbose=True, json_logs=True)
    configure_logging(verbose=False, json_logs=False)
    configure_logging(quiet=True)


def test_configure_logging_is_idempotent() -> None:
    """Repeated calls must not stack duplicate handlers, which would print
    every line once per call."""
    root = logging.getLogger()
    configure_logging()
    after_first = len(root.handlers)
    configure_logging()
    configure_logging()
    assert len(root.handlers) == after_first


def test_configure_logging_leaves_foreign_handlers_alone() -> None:
    """It must never use basicConfig(force=True) or clear root.handlers.

    pytest's caplog captures through a handler on the root logger; removing
    foreign handlers would silently break every caplog-based test in the suite.
    """
    root = logging.getLogger()
    foreign = _Capture()
    root.addHandler(foreign)
    try:
        configure_logging()
        assert foreign in root.handlers
    finally:
        root.removeHandler(foreign)


def test_configure_logging_records_propagate_to_root_for_caplog(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The 'archguard' logger must keep propagate=True.

    tests/unit/test_audit_logger.py, test_coupling.py and test_gemini_client.py
    all assert on caplog.text for archguard.* loggers, and caplog captures via
    a root handler. Setting propagate=False anywhere on that path would break
    all three.
    """
    configure_logging()
    with caplog.at_level(logging.WARNING):
        logging.getLogger("archguard.some.module").warning("propagated")
    assert "propagated" in caplog.text


# ---------------------------------------------------------------------------
# C1 regression: the web app configures logging on startup
# ---------------------------------------------------------------------------


def test_web_app_enables_info_logging_for_archguard_on_startup(
    web_client: TestClient,
) -> None:
    """C1: without configure_logging() in the lifespan, archguard.http inherits
    the root logger's default WARNING and every access-log line is discarded."""
    assert logging.getLogger("archguard.http").isEnabledFor(logging.INFO)
    assert logging.getLogger("archguard.startup").isEnabledFor(logging.INFO)


def test_web_app_installs_a_log_handler_on_startup(web_client: TestClient) -> None:
    """C1: with no handler anywhere, WARNING+ records fall through to
    logging.lastResort (bare message, no timestamp, no logger name) and INFO
    records are dropped entirely."""
    from archguard.observability.logger import owns_handler

    root = logging.getLogger()
    assert any(owns_handler(h) for h in root.handlers)


def test_request_emits_an_access_log_record(
    web_client: TestClient, capture_root: _Capture
) -> None:
    """The whole point of C1: a request must produce an access-log line."""
    response = web_client.get("/health")
    assert response.status_code == 200

    access = [r for r in capture_root.records if r.name == "archguard.http"]
    assert access, (
        "no archguard.http record was emitted for a request -- the access log "
        "is not reaching any handler"
    )
    assert "/health" in access[0].getMessage()


# ---------------------------------------------------------------------------
# Correlation id
# ---------------------------------------------------------------------------


def test_access_log_record_carries_the_response_correlation_id(
    web_client: TestClient, capture_root: _Capture
) -> None:
    response = web_client.get("/health")
    header = response.headers["X-Correlation-ID"]

    access = [r for r in capture_root.records if r.name == "archguard.http"]
    assert access
    assert getattr(access[0], "correlation_id", None) == header


def test_every_record_during_a_request_carries_the_correlation_id(
    web_client: TestClient, capture_root: _Capture
) -> None:
    """Not just the middleware's own line.

    /health is served by a route that the middleware wraps, so any record
    emitted while it runs must carry the same id the response reports.
    """
    response = web_client.get("/health")
    header = response.headers["X-Correlation-ID"]

    during = [
        r
        for r in capture_root.records
        if getattr(r, "correlation_id", NO_CORRELATION_ID) != NO_CORRELATION_ID
    ]
    assert during, "no record carried a correlation id at all"
    assert all(r.correlation_id == header for r in during)


def test_correlation_id_reaches_records_created_off_the_event_loop() -> None:
    """asyncio.to_thread copies the context; loop.run_in_executor(None, ...)
    does not. The analysis pipeline offloads its blocking work, so this is what
    decides whether analysis logs are attributable to a request at all."""
    configure_logging()
    captured: list[str] = []

    def emit_from_worker() -> None:
        record = logging.getLogger("archguard.worker.test").makeRecord(
            "archguard.worker.test", logging.INFO, __file__, 1, "in a thread", None, None
        )
        captured.append(getattr(record, "correlation_id", NO_CORRELATION_ID))

    async def scenario() -> None:
        token = correlation_id_var.set("thr00001")
        try:
            await asyncio.to_thread(emit_from_worker)
        finally:
            correlation_id_var.reset(token)

    asyncio.run(scenario())
    assert captured == ["thr00001"]


def test_records_outside_any_request_get_the_placeholder() -> None:
    configure_logging()
    record = logging.getLogger("archguard.startup").makeRecord(
        "archguard.startup", logging.INFO, __file__, 1, "no request here", None, None
    )
    assert record.correlation_id == NO_CORRELATION_ID


# ---------------------------------------------------------------------------
# uvicorn interaction
# ---------------------------------------------------------------------------


def test_an_explicit_extra_correlation_id_is_rejected_by_logging() -> None:
    """Pins a consequence of stamping via the record factory.

    Every record now arrives already carrying the key, so Logger.makeRecord's
    overwrite guard fires. Nothing in the package passes ``extra=``; this test
    exists so the next person to try it finds the reason rather than a puzzle.
    """
    configure_logging()
    with pytest.raises(KeyError, match="correlation_id"):
        logging.getLogger("archguard.extra.test").info("m", extra={"correlation_id": "x"})


def test_structured_formatter_includes_the_exception() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record(exc_info=sys.exc_info())
    parsed = json.loads(StructuredFormatter().format(record))
    assert "ValueError: boom" in parsed["exception"]


def test_console_formatter_renders_the_correlation_id() -> None:
    record = _record()
    record.__dict__["correlation_id"] = "req00001"
    line = ConsoleFormatter().format(record)
    assert "req00001" in line
    assert "hello world" in line
    assert "archguard.test" in line


def test_console_formatter_tolerates_a_record_with_no_correlation_id() -> None:
    """Its format string references %(correlation_id)s, so a record that never
    passed through the factory would raise KeyError mid-emit."""
    assert NO_CORRELATION_ID in ConsoleFormatter().format(_record())


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"level": logging.DEBUG}, logging.DEBUG),
        ({"level": "WARNING"}, logging.WARNING),
        ({"verbose": True}, logging.DEBUG),
        ({"quiet": True}, logging.ERROR),
        # quiet wins over verbose, matching the CLI's documented behaviour
        ({"quiet": True, "verbose": True}, logging.ERROR),
        ({}, logging.INFO),
    ],
)
def test_level_resolution(
    kwargs: dict[str, object], expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    configure_logging(**kwargs)  # type: ignore[arg-type]
    assert logging.getLogger("archguard").level == expected


def test_log_level_env_var_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")
    configure_logging()
    assert logging.getLogger("archguard").level == logging.DEBUG


def test_unrecognised_log_level_falls_back_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "CHATTY")
    configure_logging()
    assert logging.getLogger("archguard").level == logging.INFO


def test_production_environment_forces_json_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    configure_logging()
    assert isinstance(_our_handler().formatter, StructuredFormatter)


def test_json_logs_can_be_forced_off_even_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    configure_logging(json_logs=False)
    assert isinstance(_our_handler().formatter, ConsoleFormatter)


def test_uvicorn_access_logger_is_silenced(web_client: TestClient) -> None:
    """archguard.http supersedes uvicorn.access: it carries the correlation id,
    the request duration and the proxy-aware client IP. Leaving both enabled
    logs every request twice, in two formats, on two different streams
    (uvicorn.access writes to stdout, everything else to stderr)."""
    assert not logging.getLogger("uvicorn.access").isEnabledFor(logging.INFO)
