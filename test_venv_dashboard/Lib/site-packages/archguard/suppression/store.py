"""JSONL read/write with file locking for suppression store."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from archguard.cache.locking import file_lock
from archguard.config import EVENT_SUPPRESSION_CREATED, SUPPRESSION_FILE
from archguard.suppression.models import (
    Suppression,
    make_violation_hash,
    suppression_from_dict,
    suppression_to_jsonl,
)

logger: logging.Logger = logging.getLogger(__name__)

REASON_MAX_LENGTH: int = 500


class SuppressionValidationError(Exception):
    """Raised when suppression input fails validation."""


class SuppressionStore:
    """Append-only JSONL store for violation suppressions."""

    def __init__(self, repo_root: Path) -> None:
        self._path: Path = repo_root / SUPPRESSION_FILE
        self._lock_path: Path = self._path.with_suffix(".lock")
        self._cache: list[Suppression] | None = None
        self._cache_mtime: float = 0.0

    def add(
        self,
        module: str,
        layer: int,
        message: str,
        reason: str,
        created_by: str = "local",
        expires_at: str | None = None,
        pr_number: int | None = None,
        commit_sha: str = "unknown",
    ) -> Suppression:
        """Add a new suppression record.

        Validates reason length, newlines, and layer range before writing.
        """
        # Validation
        if len(reason) > REASON_MAX_LENGTH:
            raise SuppressionValidationError(
                f"reason exceeds 500 characters ({len(reason)} chars)"
            )
        if "\n" in reason:
            raise SuppressionValidationError("reason must not contain newlines")
        if layer not in {1, 2, 3, 4}:
            raise SuppressionValidationError("layer must be 1, 2, 3, or 4")

        suppression = Suppression(
            id=str(uuid.uuid4()),
            module=module,
            layer=layer,
            violation_hash=make_violation_hash(module, layer, message),
            reason=reason,
            created_at=datetime.now(timezone.utc).isoformat(),
            created_by=created_by,
            expires_at=expires_at,
            pr_number=pr_number,
            commit_sha=commit_sha,
            active=True,
        )

        self._path.parent.mkdir(parents=True, exist_ok=True)

        with file_lock(self._lock_path):
            with self._path.open("a", encoding="utf-8") as f:
                f.write(suppression_to_jsonl(suppression) + "\n")

        # Audit logging (best-effort)
        try:
            from archguard.audit.logger import AuditLogger

            audit = AuditLogger()
            audit.log(
                EVENT_SUPPRESSION_CREATED,
                suppression_id=suppression.id,
                module=module,
                layer=layer,
            )
        except Exception as e:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).warning(
                f"Non-critical failure in audit log suppression: {e}"
            )

        self._cache = None  # Force reload on next access
        return suppression

    def _force_reload(self) -> None:
        """Invalidate the cache to force a reload from disk on the next read."""
        self._cache = None
        self._cache_mtime = 0.0

    def _load_cache(self) -> list[Suppression]:
        """Load suppressions from JSONL, using mtime to detect changes.

        Callers holding a file lock must call _force_reload() first to ensure
        they do not read stale data that was cached before the lock was acquired.
        """
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            return []

        if self._cache is not None and mtime == self._cache_mtime:
            return self._cache

        suppressions: list[Suppression] = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data: dict[str, Any] = json.loads(line)
                    suppressions.append(suppression_from_dict(data))
                except Exception as e:  # noqa: BLE001
                    import logging

                    logging.getLogger(__name__).warning(
                        f"Non-critical failure in suppression line parse: {e}"
                    )
                    logger.warning("Skipping malformed suppression line")
                    continue

        self._cache = suppressions
        self._cache_mtime = mtime
        return self._cache

    def list_all(self, include_inactive: bool = False) -> list[Suppression]:
        """Read all suppressions from the JSONL file.

        Skips malformed lines. Filters active=True unless *include_inactive*.
        Returns sorted by created_at descending.
        """
        all_sups = self._load_cache()
        results = [sup for sup in all_sups if include_inactive or sup.active]
        results.sort(key=lambda s: s.created_at, reverse=True)
        return results

    def is_suppressed(self, module: str, layer: int, message: str) -> bool:
        """Return True if an active, non-expired suppression matches."""
        target_hash = make_violation_hash(module, layer, message)
        now = datetime.now(timezone.utc)

        for sup in self._load_cache():
            if sup.violation_hash != target_hash:
                continue
            if not sup.active:
                continue
            if sup.expires_at is not None:
                try:
                    expiry = datetime.fromisoformat(sup.expires_at)
                    if now > expiry:
                        continue
                except ValueError:
                    continue
            return True

        return False

    def detect_orphans(self, active_modules: list[str]) -> list[Suppression]:
        """Return suppressions whose module is not in *active_modules*."""
        module_set = set(active_modules)
        return [s for s in self.list_all() if s.module not in module_set]

    def mark_orphans(self, orphan_ids: list[str]) -> int:
        """Rewrite JSONL setting active=False for given IDs."""
        if not self._path.exists():
            return 0

        id_set = set(orphan_ids)
        count = 0

        with file_lock(self._lock_path):
            self._force_reload()
            all_sups = self._read_all_raw()
            updated: list[Suppression] = []
            for sup in all_sups:
                if sup.id in id_set and sup.active:
                    updated.append(replace(sup, active=False))
                    count += 1
                else:
                    updated.append(sup)
            self._write_all_raw(updated)

        return count

    def migrate_module(self, old_name: str, new_name: str) -> int:
        """Rewrite JSONL updating module field and recalculating violation_hash."""
        if not self._path.exists():
            return 0

        count = 0

        with file_lock(self._lock_path):
            self._force_reload()
            all_sups = self._read_all_raw()
            updated: list[Suppression] = []
            for sup in all_sups:
                if sup.module == old_name:
                    # Reverse-engineer message from hash is impossible,
                    # so recalculate with the new module name using same reason
                    new_hash = make_violation_hash(
                        new_name,
                        sup.layer,
                        sup.reason,
                    )
                    updated.append(
                        replace(
                            sup,
                            module=new_name,
                            violation_hash=new_hash,
                        )
                    )
                    count += 1
                else:
                    updated.append(sup)
            self._write_all_raw(updated)

        return count

    def to_columnar_table(self, suppressions: list[Suppression]) -> str:
        """Return Rich-formatted table string."""
        from io import StringIO

        from rich.console import Console
        from rich.table import Table

        table = Table(show_header=True, header_style="bold")
        table.add_column("ID", width=8)
        table.add_column("Module")
        table.add_column("Layer", width=5)
        table.add_column("Reason", max_width=40)
        table.add_column("Expires")
        table.add_column("Created")

        for s in suppressions:
            reason_display = s.reason[:40] + "..." if len(s.reason) > 40 else s.reason
            expires_str = s.expires_at or "never"
            table.add_row(
                s.id[:8],
                s.module,
                str(s.layer),
                reason_display,
                expires_str,
                s.created_at[:10],
            )

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        console.print(table)
        return buf.getvalue()

    def to_json(self, suppressions: list[Suppression]) -> str:
        """Return JSON array string of all suppressions."""
        items = [json.loads(suppression_to_jsonl(s)) for s in suppressions]
        return json.dumps(items, indent=2)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_all_raw(self) -> list[Suppression]:
        """Read all lines without filtering."""
        return self._load_cache()

    def _write_all_raw(self, suppressions: list[Suppression]) -> None:
        """Overwrite entire file with given suppressions."""
        self._force_reload()
        with self._path.open("w", encoding="utf-8") as f:
            for s in suppressions:
                f.write(suppression_to_jsonl(s) + "\n")
        self._force_reload()
