"""Unit tests for archguard.suppression.store."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from archguard.config import SUPPRESSION_FILE
from archguard.suppression.models import make_violation_hash
from archguard.suppression.store import (
    REASON_MAX_LENGTH,
    SuppressionStore,
    SuppressionValidationError,
)


def _make_store(tmp_path: Path) -> SuppressionStore:
    return SuppressionStore(tmp_path)


class TestSuppressionAdd:
    def test_valid_add_readable(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        s = store.add("payments", 1, "bad import", "tech debt")
        assert s.module == "payments"
        assert s.layer == 1
        assert s.active is True
        # Verify readable from file
        loaded = store.list_all()
        assert len(loaded) == 1
        assert loaded[0].id == s.id

    def test_reason_too_long(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        long_reason = "x" * 501
        with pytest.raises(SuppressionValidationError, match="exceeds 500"):
            store.add("mod", 1, "msg", long_reason)

    def test_reason_with_newline(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(
            SuppressionValidationError, match="must not contain newlines",
        ):
            store.add("mod", 1, "msg", "line1\nline2")

    def test_invalid_layer(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(
            SuppressionValidationError, match="layer must be 1, 2, 3, or 4",
        ):
            store.add("mod", 5, "msg", "reason")


class TestSuppressionList:
    def test_no_file_returns_empty(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.list_all() == []

    def test_malformed_line_skipped(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        # Write a valid suppression first
        store.add("mod", 1, "msg", "reason")
        # Append a malformed line
        sup_file = tmp_path / SUPPRESSION_FILE
        with sup_file.open("a", encoding="utf-8") as f:
            f.write("{bad json\n")
        # Should still return the valid one
        loaded = store.list_all()
        assert len(loaded) == 1


class TestIsSuppressed:
    def test_matching_active_suppression(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.add("payments", 1, "bad import", "known debt")
        assert store.is_suppressed("payments", 1, "bad import") is True

    def test_expired_suppression_not_matched(
        self, tmp_path: Path,
    ) -> None:
        store = _make_store(tmp_path)
        past = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        store.add(
            "payments", 1, "bad import", "temp",
            expires_at=past,
        )
        assert store.is_suppressed("payments", 1, "bad import") is False

    def test_inactive_suppression_not_matched(
        self, tmp_path: Path,
    ) -> None:
        store = _make_store(tmp_path)
        s = store.add("payments", 1, "bad import", "old debt")
        store.mark_orphans([s.id])
        assert store.is_suppressed("payments", 1, "bad import") is False

    def test_is_suppressed_reads_file_once_for_multiple_violations(
        self, tmp_path: Path
    ) -> None:
        from unittest.mock import patch
        import builtins
        from archguard.config import SUPPRESSION_FILE

        store = _make_store(tmp_path)
        store.add("mod", 1, "msg", "reason")

        with patch("builtins.open", wraps=builtins.open) as spy:
            for _ in range(100):
                store.is_suppressed("mod", 1, "msg")

            store_opens = [c for c in spy.call_args_list if str(SUPPRESSION_FILE) in str(c)]
            assert len(store_opens) <= 2


class TestOrphans:
    def test_detect_orphans(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.add("deleted_mod", 1, "msg", "reason")
        store.add("active_mod", 2, "msg2", "reason2")
        orphans = store.detect_orphans(["active_mod"])
        assert len(orphans) == 1
        assert orphans[0].module == "deleted_mod"

    def test_mark_orphans_sets_inactive(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        s = store.add("deleted_mod", 1, "msg", "reason")
        count = store.mark_orphans([s.id])
        assert count == 1
        all_sups = store.list_all(include_inactive=True)
        inactive = [x for x in all_sups if not x.active]
        assert len(inactive) == 1
        assert inactive[0].id == s.id


class TestMigrate:
    def test_migrate_updates_module_and_hash(
        self, tmp_path: Path,
    ) -> None:
        store = _make_store(tmp_path)
        store.add("old_mod", 1, "msg", "reason")
        count = store.migrate_module("old_mod", "new_mod")
        assert count == 1
        loaded = store.list_all()
        assert loaded[0].module == "new_mod"
        # Hash should be recalculated
        assert loaded[0].violation_hash != make_violation_hash(
            "old_mod", 1, "reason",
        )


class TestConcurrency:
    def test_concurrent_adds_no_corruption(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        def add_suppression(idx: int) -> None:
            store.add(f"mod{idx}", 1, f"msg{idx}", f"reason{idx}")

        threads = [
            threading.Thread(target=add_suppression, args=(i,))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        loaded = store.list_all()
        assert len(loaded) == 5

    def test_lock_timeout_raises(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        lock_path = store._lock_path
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        from archguard.cache.locking import file_lock

        barrier = threading.Barrier(2, timeout=5.0)
        error_holder: list[BaseException] = []

        def hold_lock() -> None:
            with file_lock(lock_path, timeout=5.0):
                barrier.wait()  # signal: lock is held
                import time
                time.sleep(1.0)  # hold for 1s

        t = threading.Thread(target=hold_lock)
        t.start()
        barrier.wait()  # wait until lock is held by the other thread

        with pytest.raises(TimeoutError):
            with file_lock(lock_path, timeout=0.3):
                pass  # pragma: no cover

        t.join()
