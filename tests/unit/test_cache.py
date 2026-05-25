"""Unit tests for archguard.cache.db."""

from __future__ import annotations

from pathlib import Path

import pytest

from archguard.cache.db import EmbeddingDB


class TestEmbeddingDB:
    """Tests for EmbeddingDB."""

    def test_creates_db_with_wal_mode(self, tmp_path: Path) -> None:
        """DB file is created and WAL mode is active."""
        db_path = tmp_path / "cache" / "test.db"
        with EmbeddingDB(db_path) as db:
            row = db._conn.execute("PRAGMA journal_mode").fetchone()
            assert row is not None
            assert row[0].lower() == "wal"
        assert db_path.exists()

    def test_set_get_meta_roundtrip(self, tmp_path: Path) -> None:
        """set_meta / get_meta round-trip works."""
        with EmbeddingDB(tmp_path / "test.db") as db:
            db.set_meta("test_key", "test_value")
            assert db.get_meta("test_key") == "test_value"

    def test_get_meta_missing_key(self, tmp_path: Path) -> None:
        """get_meta for missing key returns None."""
        with EmbeddingDB(tmp_path / "test.db") as db:
            assert db.get_meta("nonexistent") is None

    def test_context_manager_closes(self, tmp_path: Path) -> None:
        """Context manager closes connection cleanly."""
        db = EmbeddingDB(tmp_path / "test.db")
        db.__enter__()
        db.__exit__(None, None, None)
        # After close, executing should raise
        with pytest.raises(Exception):
            db._conn.execute("SELECT 1")
