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

            # HMAC for integrity verification
            entry_str = json.dumps(entry, sort_keys=True)
            import hmac
            import hashlib
            import os

            audit_secret = os.environ.get("ARCHGUARD_AUDIT_SECRET")
            if not audit_secret:
                strict_mode = os.environ.get("ARCHGUARD_AUDIT_STRICT", "").lower() in (
                    "1",
                    "true",
                )
                key_file = self._log_path.parent / "audit.key"
                if strict_mode and not key_file.exists():
                    from archguard.utils.errors import ConfigError

                    raise ConfigError(
                        "ARCHGUARD_AUDIT_STRICT is enabled, but ARCHGUARD_AUDIT_SECRET is not set and no key file exists."
                    )
                if key_file.exists():
                    audit_secret = key_file.read_text(encoding="utf-8").strip()
                else:
                    import secrets
                    import logging as _logging

                    audit_secret = secrets.token_hex(32)
                    self._log_path.parent.mkdir(parents=True, exist_ok=True)
                    # Write with 0600 permissions
                    with open(key_file, "w", encoding="utf-8") as key_f:
                        key_f.write(audit_secret)
                    try:
                        key_file.chmod(0o600)
                    except Exception:
                        pass
                    _logging.getLogger(__name__).info(
                        "Generated new audit HMAC key at %s", key_file
                    )
            secret = audit_secret.encode("utf-8")
            signature = hmac.new(
                secret, entry_str.encode("utf-8"), hashlib.sha256
            ).hexdigest()
            entry["hmac"] = signature

            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:  # noqa: BLE001 — intentionally broad
            from archguard.utils.errors import ConfigError

            if isinstance(e, ConfigError):
                raise e
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

            logging.getLogger(__name__).warning(
                f"Non-critical failure in _maybe_rotate: {e}"
            )

    def read_last_run(self) -> dict[str, Any] | None:
        """Read the audit log from the end to find the last 'analysis_run' event."""
        return read_last_run(self._log_path)

    def read_last_n_runs(self, n: int = 2) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        if not self._log_path.exists() or n <= 0:
            return runs

        try:
            with open(self._log_path, "rb") as f:
                f.seek(0, 2)
                pos = f.tell()
                buffer = bytearray()
                chunk_size = 8192
                while pos > 0 and len(runs) < n:
                    read_size = min(chunk_size, pos)
                    pos -= read_size
                    f.seek(pos, 0)
                    chunk = f.read(read_size)
                    buffer = bytearray(chunk) + buffer
                    lines = buffer.split(b"\n")
                    buffer = lines[0]
                    for line in reversed(lines[1:]):
                        if not line.strip():
                            continue
                        try:
                            entry = json.loads(line.decode("utf-8", errors="replace"))
                            if entry.get("event") == AUDIT_EVENT_ANALYSIS:
                                runs.append(entry)
                                if len(runs) == n:
                                    break
                        except json.JSONDecodeError:
                            pass

                if len(runs) < n and buffer.strip():
                    try:
                        entry = json.loads(buffer.decode("utf-8", errors="replace"))
                        if entry.get("event") == AUDIT_EVENT_ANALYSIS:
                            runs.append(entry)
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(
                f"Non-critical failure in read_last_n_runs: {e}"
            )

        return list(reversed(runs))


def read_last_run(log_path: Path) -> dict[str, Any] | None:
    """Return the most recent analysis_run event, or None if not found."""
    if not log_path.exists():
        return None
    try:
        from archguard.config import AUDIT_EVENT_ANALYSIS

        with open(log_path, "rb") as f:
            f.seek(0, 2)
            pos = f.tell()
            buffer = bytearray()
            chunk_size = 8192
            while pos > 0:
                read_size = min(chunk_size, pos)
                pos -= read_size
                f.seek(pos, 0)
                chunk = f.read(read_size)
                buffer = bytearray(chunk) + buffer
                lines = buffer.split(b"\n")
                buffer = lines[0]
                for line in reversed(lines[1:]):
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line.decode("utf-8", errors="replace"))
                        if event.get("event") == AUDIT_EVENT_ANALYSIS:
                            from typing import cast

                            return cast(dict[str, Any], event)
                    except json.JSONDecodeError:
                        continue

            if buffer.strip():
                try:
                    event = json.loads(buffer.decode("utf-8", errors="replace"))
                    if event.get("event") == AUDIT_EVENT_ANALYSIS:
                        from typing import cast

                        return cast(dict[str, Any], event)
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            f"Non-critical failure in read_last_run: {e}"
        )
    return None


def serialize_fitness_results(
    fitness_results: list[Any],
    configs: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Serialize fitness function results for audit log persistence.

    Args:
        fitness_results: List of FitnessFunctionResult objects.
        configs: Optional list of FitnessFunctionConfig objects for metadata.

    Returns:
        List of dicts suitable for JSON serialization in audit logs.
    """
    config_map: dict[str, Any] = {}
    if configs:
        for c in configs:
            config_map[getattr(c, "rule", "")] = c

    serialized: list[dict[str, Any]] = []
    for fr in fitness_results:
        rule = getattr(fr, "rule", "")
        cfg = config_map.get(rule)
        entry: dict[str, Any] = {
            "name": getattr(cfg, "name", rule) if cfg else rule,
            "rule": rule,
            "passed": getattr(fr, "passed", True),
            "severity": getattr(cfg, "severity", "warn") if cfg else "warn",
            "evidence": getattr(fr, "details", None)
            or getattr(fr, "error", None)
            or "",
            "rationale": getattr(cfg, "rationale", "") if cfg else "",
        }
        serialized.append(entry)
    return serialized
