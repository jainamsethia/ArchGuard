"""SQLite WAL-mode database for ArchGuard embedding and centroid caches."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION: str = "1.0"

SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS archguard_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
    file_path     TEXT NOT NULL,
    function_name TEXT NOT NULL,
    embedding     BLOB NOT NULL,
    content_hash  TEXT NOT NULL,
    model_name    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (file_path, function_name)
);

CREATE TABLE IF NOT EXISTS module_centroids (
    module_name   TEXT PRIMARY KEY,
    centroid      BLOB NOT NULL,
    content_hash  TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""


class EmbeddingDB:
    """SQLite database with WAL mode for embedding storage."""

    def __init__(self, db_path: Path) -> None:
        """Open (or create) the database at *db_path*.

        * Creates parent directories automatically.
        * Enables WAL journal mode + performance pragmas.
        * Runs schema migrations.
        * Records schema version in ``archguard_meta``.
        """
        import logging
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn: sqlite3.Connection = sqlite3.connect(str(db_path), timeout=10)
        except sqlite3.DatabaseError as e:
            logging.warning(f"Cache database corrupted ({e}). Recreating cache.")
            db_path.unlink(missing_ok=True)
            self._conn = sqlite3.connect(str(db_path), timeout=10)

        # Enable WAL mode and performance pragmas
        self._conn.execute("PRAGMA journal_mode=WAL")
        result = self._conn.execute("PRAGMA journal_mode").fetchone()
        if result and result[0] != "wal":
            logging.warning("SQLite WAL mode unavailable (network filesystem?). Using default journal mode.")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.commit()

        # Create schema
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

        # Store schema version
        self.set_meta("schema_version", SCHEMA_VERSION)

    def get_meta(self, key: str) -> str | None:
        """Get a metadata value by key.  Returns ``None`` if not found."""
        cursor = self._conn.execute(
            "SELECT value FROM archguard_meta WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Set a metadata value (insert-or-replace)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO archguard_meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> EmbeddingDB:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
