"""JSONL append logger with rotation for audit events."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from archguard.config import (
    AUDIT_LOG_FILENAME,
    AUDIT_LOG_MAX_BYTES,
    AUDIT_LOG_MAX_ENTRIES,
    AUDIT_EVENT_ANALYSIS,
)



class AuditLogger:
    """Append-only JSONL audit logger with automatic rotation."""

    def __init__(self, log_path: Path | None = None) -> None:
        self._log_path: Path = log_path or Path(AUDIT_LOG_FILENAME)

    def log(self, event: str, **kwargs: Any) -> None:
        """Append a JSON line to the audit log.

        Rotates when the file exceeds size or entry-count limits.
        Silently swallows all exceptions so audit logging never crashes the CLI.
        """
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            self._maybe_rotate(self._log_path)

            entry: dict[str, Any] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event,
                **kwargs,
            }

            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:  # noqa: BLE001 — intentionally broad
            import logging
            logging.getLogger(__name__).warning(f"Non-critical failure in log: {e}")

    def _maybe_rotate(self, log_path: Path) -> None:
        """Rotate log file by truncating to the last MAX_ENTRIES lines."""
        if not log_path.exists():
            return
        try:
            size = log_path.stat().st_size
            if size < AUDIT_LOG_MAX_BYTES:
                return
            # Count lines
            with open(log_path, encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) < AUDIT_LOG_MAX_ENTRIES:
                return
            # Keep only the last MAX_ENTRIES - 1 lines
            keep_count = AUDIT_LOG_MAX_ENTRIES - 1
            if keep_count > 0:
                with open(log_path, "w", encoding="utf-8") as f:
                    f.writelines(lines[-keep_count:])
            else:
                # If MAX_ENTRIES is 1, we just clear the file
                with open(log_path, "w", encoding="utf-8") as f:
                    pass
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(f"Non-critical failure in _maybe_rotate: {e}")

    def read_last_run(self) -> dict[str, Any] | None:
        """Read the audit log from the end to find the last 'analysis_run' event."""
        return read_last_run(self._log_path)

def read_last_run(log_path: Path) -> dict[str, Any] | None:
    """Return the most recent analysis_run event, or None if not found."""
    if not log_path.exists():
        return None
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in reversed(f.readlines()):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    if event.get("event") == AUDIT_EVENT_ANALYSIS:
                        return event
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Non-critical failure in read_last_run: {e}")
    return None
