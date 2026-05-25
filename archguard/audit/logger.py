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
)

_MAX_GENERATIONS: int = 3


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

            if self._log_path.exists() and self._should_rotate(self._log_path):
                self._rotate(self._log_path)

            entry: dict[str, Any] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event,
                **kwargs,
            }

            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:  # noqa: BLE001 — intentionally broad
            pass  # Never crash CLI due to audit failure

    def _should_rotate(self, path: Path) -> bool:
        """Return True if the log file exceeds size or entry-count limits."""
        try:
            if path.stat().st_size >= AUDIT_LOG_MAX_BYTES:
                return True
            with path.open("r", encoding="utf-8") as f:
                line_count = sum(1 for _ in f)
            return line_count >= AUDIT_LOG_MAX_ENTRIES
        except Exception:  # noqa: BLE001
            return False

    def _rotate(self, path: Path) -> None:
        """Rotate log files, keeping up to 3 generations.

        audit.jsonl.2 → audit.jsonl.3 (deleted if exists)
        audit.jsonl.1 → audit.jsonl.2
        audit.jsonl   → audit.jsonl.1
        """
        try:
            for i in range(_MAX_GENERATIONS, 0, -1):
                src = path.with_suffix(f"{path.suffix}.{i}")
                dst = path.with_suffix(f"{path.suffix}.{i + 1}")
                if i == _MAX_GENERATIONS and dst.exists():
                    dst.unlink()
                if src.exists():
                    src.rename(dst)
            path.rename(path.with_suffix(f"{path.suffix}.1"))
        except Exception:  # noqa: BLE001
            pass
