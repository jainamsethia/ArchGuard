"""Unit tests for archguard.suppression.store."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from archguard.config import SUPPRESSION_FILE
from archguard.suppression.models import make_violation_hash
from archguard.suppression.store import (
    SuppressionStore,
    SuppressionValidationError,
)


def _make_store(tmp_path: Path) -> SuppressionStore:
    return SuppressionStore(tmp_path)


class TestSuppressionAdd:
    def test_add_persists_across_new_instance(self, tmp_path: Path) -> None:
        store1 = _make_store(tmp_path)
        store1.add("payments", 1, "bad import", "tech debt")

        store2 = _make_store(tmp_path)
        loaded = store2.list_all()
        assert len(loaded) == 1
        assert loaded[0].module == "payments"
        assert loaded[0].layer == 1

    def test_is_suppressed_returns_false_for_non_suppressed(
        self, tmp_path: Path
    ) -> None:
        store = _make_store(tmp_path)
        store.add("payments", 1, "bad import", "tech debt")

        assert store.is_suppressed("payments", 1, "different import") is False
        assert store.is_suppressed("other_module", 1, "bad import") is False
        assert store.is_suppressed("payments", 2, "bad import") is False

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
            SuppressionValidationError,
            match="must not contain newlines",
        ):
            store.add("mod", 1, "msg", "line1\nline2")

    def test_invalid_layer(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(
            SuppressionValidationError,
            match="layer must be 1, 2, 3, or 4",
        ):
            store.add("mod", 5, "msg", "reason")

    def test_layer4_violations_can_be_suppressed(self, tmp_path: Path) -> None:
        """After Bug N-1 fix, Layer 4 violations must be suppressable."""
        from archguard.suppression.store import SuppressionStore

        store = SuppressionStore(tmp_path)
        store.add(
            module="api", layer=4, message="Duplicate function body", reason="test"
        )
        assert store.is_suppressed("api", 4, "Duplicate function body") is True
        assert store.is_suppressed("api", 4, "Different message") is False


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
        self,
        tmp_path: Path,
    ) -> None:
        store = _make_store(tmp_path)
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        store.add(
            "payments",
            1,
            "bad import",
            "temp",
            expires_at=past,
        )
        assert store.is_suppressed("payments", 1, "bad import") is False

    def test_inactive_suppression_not_matched(
        self,
        tmp_path: Path,
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

            store_opens = [
                c for c in spy.call_args_list if str(SUPPRESSION_FILE) in str(c)
            ]
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


class TestDelete:
    def test_delete_removes_matching_record(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        s = store.add("payments", 1, "bad import", "tech debt")
        store.add("billing", 2, "coupling", "reason2")

        removed = store.delete(s.id)

        assert removed is True
        remaining = store.list_all(include_inactive=True)
        assert len(remaining) == 1
        assert remaining[0].module == "billing"

    def test_delete_returns_false_when_missing(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.add("payments", 1, "bad import", "tech debt")

        removed = store.delete("nonexistent-id")

        assert removed is False
        assert len(store.list_all(include_inactive=True)) == 1

    def test_delete_returns_false_when_no_store_file(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.delete("any-id") is False


class TestMigrate:
    def test_migrate_updates_module_and_hash(
        self,
        tmp_path: Path,
    ) -> None:
        store = _make_store(tmp_path)
        store.add("old_mod", 1, "msg", "reason")
        count = store.migrate_module("old_mod", "new_mod")
        assert count == 1
        loaded = store.list_all()
        assert loaded[0].module == "new_mod"
        # Hash should be recalculated
        assert loaded[0].violation_hash != make_violation_hash(
            "old_mod",
            1,
            "reason",
        )


class TestConcurrency:
    def test_concurrent_adds_no_corruption(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        def add_suppression(idx: int) -> None:
            store.add(f"mod{idx}", 1, f"msg{idx}", f"reason{idx}")

        threads = [
            threading.Thread(target=add_suppression, args=(i,)) for i in range(5)
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

    def test_mark_orphans_toctou_dataloss(self, tmp_path: Path) -> None:
        """Test that mark_orphans doesn't use stale cache leading to data loss.

        Thread B appends a new suppression. Thread A's mark_orphans shouldn't
        overwrite it due to stale cache.
        """
        store = _make_store(tmp_path)
        s1 = store.add("mod1", 1, "msg1", "reason1")

        # Populate thread A's cache
        store.list_all()

        # We need to simulate Thread B adding a suppression *while* Thread A is
        # executing mark_orphans, specifically after cache is loaded but before it writes.
        # But since add() locks, Thread B will block until Thread A is done, OR Thread B
        # executes before Thread A gets the lock.
        # Let's patch _read_all_raw to have Thread B append to the file directly to simulate
        # a TOCTOU race (e.g. bypassing the lock or NFS delays).

        original_read = store._read_all_raw

        def mock_read():
            # This simulates Thread B appending a suppression exactly between
            # Thread A acquiring the lock and reading/writing the data!
            # Since Thread B is "another process", we just append to the file.
            with store._path.open("a", encoding="utf-8") as f:
                from archguard.suppression.models import (
                    suppression_to_jsonl,
                    Suppression,
                )
                import uuid
                from datetime import datetime, timezone
                from archguard.suppression.models import make_violation_hash

                s2 = Suppression(
                    id=str(uuid.uuid4()),
                    module="mod2",
                    layer=1,
                    violation_hash=make_violation_hash("mod2", 1, "msg2"),
                    reason="r2",
                    created_at=datetime.now(timezone.utc).isoformat(),
                    created_by="test",
                    expires_at=None,
                    pr_number=None,
                    commit_sha="unknown",
                    active=True,
                )
                f.write(suppression_to_jsonl(s2) + "\n")

            return original_read()

        from unittest.mock import patch

        with patch.object(store, "_read_all_raw", side_effect=mock_read):
            store.mark_orphans([s1.id])

        # If _force_reload() wasn't called at the start of mark_orphans,
        # _read_all_raw would return the old cache [s1], and then write_all_raw
        # would overwrite the file with ONLY s1 (modified), deleting s2!
        # With _force_reload(), mock_read's original_read() will parse the new file
        # and see s2!

        loaded = store.list_all(include_inactive=True)
        assert len(loaded) == 2
        assert any(s.module == "mod2" for s in loaded)
