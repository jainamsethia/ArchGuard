"""JSONL append logger with rotation for audit events."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from archguard.config import (
    AUDIT_EVENT_ANALYSIS,
    AUDIT_LOG_FILENAME,
    AUDIT_LOG_MAX_BYTES,
)

logger = logging.getLogger(__name__)

#: Signed entries are serialised with exactly these options, in both the signing
#: and the verifying direction. ``default=str`` matters: without it any value
#: json cannot represent natively (a datetime, a Path) raised TypeError during
#: signing and silently dropped the whole entry; with it, signing and writing
#: no longer produce different bytes, which made every signature unverifiable.
_JSON_KWARGS: dict[str, Any] = {"sort_keys": True, "default": str}


def _canonical(entry: dict[str, Any]) -> str:
    """Serialise *entry* (minus its signature) to the exact signed byte string."""
    return json.dumps(
        {k: v for k, v in entry.items() if k != "hmac"}, **_JSON_KWARGS
    )


def _sign(secret: str, entry: dict[str, Any]) -> str:
    return hmac.new(
        secret.encode("utf-8"), _canonical(entry).encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_entry(entry: dict[str, Any], secret: str) -> bool:
    """Return True iff *entry* carries a signature matching *secret*.

    Entries written before signing existed (no ``hmac`` key) return False: an
    unsigned entry is not a verified one.
    """
    recorded = entry.get("hmac")
    if not isinstance(recorded, str):
        return False
    return hmac.compare_digest(_sign(secret, entry), recorded)


def resolve_audit_secret(log_dir: Path, create: bool = False) -> str | None:
    """Return the HMAC secret for the log in *log_dir*, or None if there is none.

    Resolution order: ``ARCHGUARD_AUDIT_SECRET``, then ``audit.key`` beside the
    log. When *create* is True a new key file is generated if neither exists.
    """
    from_env = os.environ.get("ARCHGUARD_AUDIT_SECRET")
    if from_env:
        return from_env

    key_file = log_dir / "audit.key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    if not create:
        return None

    secret = secrets.token_hex(32)
    log_dir.mkdir(parents=True, exist_ok=True)
    key_file.write_text(secret, encoding="utf-8")
    try:
        key_file.chmod(0o600)
    except OSError as chmod_exc:
        # Windows and some network filesystems don't honour POSIX modes. The
        # key is still written, but it is not owner-only -- say so rather than
        # implying it is.
        logger.warning(
            "Could not restrict permissions on audit key %s (%s). "
            "The HMAC key may be readable by other users on this host.",
            key_file, chmod_exc,
        )
    logger.info("Generated new audit HMAC key at %s", key_file)
    return secret


class AuditLogger:
    """Append-only JSONL audit logger with automatic rotation."""

    def __init__(self, log_path: Path | None = None) -> None:
        self._log_path: Path = log_path or Path(AUDIT_LOG_FILENAME)

    def log(self, event: str, **kwargs: Any) -> None:
        """Append a JSON line to the audit log.

        Rotates when the file exceeds size or entry-count limits.
        Silently swallows all exceptions so audit logging never crashes the CLI.
        """
        strict = os.environ.get("ARCHGUARD_AUDIT_STRICT", "").lower() in ("1", "true")
        try:
            log_dir = self._log_path.parent
            log_dir.mkdir(parents=True, exist_ok=True)
            self._maybe_rotate(self._log_path)

            entry: dict[str, Any] = {
                "timestamp": datetime.now(UTC).isoformat(),
                "event": event,
                **kwargs,
            }

            if strict and resolve_audit_secret(log_dir) is None:
                from archguard.utils.errors import ConfigError

                raise ConfigError(
                    "ARCHGUARD_AUDIT_STRICT is enabled, but "
                    "ARCHGUARD_AUDIT_SECRET is not set and no key file exists."
                )
            audit_secret = resolve_audit_secret(log_dir, create=True)
            assert audit_secret is not None  # create=True always yields a key
            entry["hmac"] = _sign(audit_secret, entry)

            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, **_JSON_KWARGS) + "\n")
        except Exception as exc:
            # Audit logging must never crash the caller in the default mode.
            # (`strict` is read before the try block on purpose: it used to be
            # read here via an `os` imported *inside* the try, so any failure
            # before that import raised UnboundLocalError from the handler and
            # masked the real error.)
            logger.warning("Audit log operation failed (non-fatal): %s", exc)
            if strict:
                raise

    def log_run(self, repo_url: str, job_id: str, **kwargs: Any) -> None:
        """Helper to log an analysis run for the dashboard."""
        from archguard.config import AUDIT_EVENT_ANALYSIS
        self.log(
            event=AUDIT_EVENT_ANALYSIS,
            repo_url=repo_url,
            job_id=job_id,
            **kwargs
        )

    def _maybe_rotate(self, log_path: Path) -> None:
        """Roll the log over to ``<name>.1`` once it exceeds the size cap.

        This used to truncate in place, keeping only the last 999 lines and
        deleting everything before them. For a tamper-evident security trail
        that is not housekeeping, it is silent evidence destruction: the entries
        an investigation would want are the oldest ones. One generation is kept
        (``audit.jsonl.1``, replaced on the next rotation), which bounds disk use
        without the active file ever deleting its own history.
        """
        if not log_path.exists():
            return
        try:
            if log_path.stat().st_size < AUDIT_LOG_MAX_BYTES:
                return
            archive = log_path.with_name(log_path.name + ".1")
            archive.unlink(missing_ok=True)
            log_path.replace(archive)
        except Exception as e:
            logger.warning(
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
            logger.warning(
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
        logger.warning(
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
