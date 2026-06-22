"""Unit tests for archguard.observability.logger."""

from __future__ import annotations

import json
import logging

from archguard.observability.logger import StructuredFormatter, configure_logging


def test_structured_formatter_produces_valid_json() -> None:
    record = logging.LogRecord(
        name="archguard.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    formatted = StructuredFormatter().format(record)
    parsed = json.loads(formatted)
    assert parsed["message"] == "hello world"
    assert parsed["level"] == "INFO"


def test_configure_logging_does_not_raise(capsys) -> None:
    configure_logging(verbose=True, json_logs=True)
    configure_logging(verbose=False, json_logs=False)
