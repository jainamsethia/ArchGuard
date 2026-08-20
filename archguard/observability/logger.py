import json
import logging
import sys
from datetime import UTC, datetime


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


def configure_logging(verbose: bool = False, quiet: bool = False, json_logs: bool = False) -> None:
    if quiet:
        level = logging.ERROR
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    handler = logging.StreamHandler(sys.stderr)

    # Enable JSON logs if explicitly requested or if stderr is not a TTY (e.g. running in CI/piped)
    if json_logs or not sys.stderr.isatty():
        handler.setFormatter(StructuredFormatter())

    logging.basicConfig(level=level, handlers=[handler])
