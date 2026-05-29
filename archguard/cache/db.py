"""SQLite WAL-mode database for ArchGuard embedding and centroid caches."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from typing import Callable

CURRENT_SCHEMA_VERSION = 2  # increment when schema changes

MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {}

def migration(version: int) -> Callable[[Callable[[sqlite3.Connection], None]], Callable[[sqlite3.Connection], None]]:
    """Decorator to register a migration function."""
    def decorator(fn: Callable[[sqlite3.Connection], None]) -> Callable[[sqlite3.Connection], None]:
        MIGRATIONS[version] = fn
        return fn
    return decorator

@migration(1)
def _migrate_v1(conn: sqlite3.Connection) -> None:
    """Create initial schema."""
    conn.executescript("""
CREATE TABLE IF NOT EXISTS archguard_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
    file_path     TEXT NOT NULL,
    function_name TEXT NOT NULL,
    embedding     BLOB NOT NULL,
    content_hash  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (file_path, function_name)
);

CREATE TABLE IF NOT EXISTS module_centroids (
    module_name   TEXT PRIMARY KEY,
    centroid      BLOB NOT NULL,
    content_hash  TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
""")

@migration(2)
def _migrate_v2(conn: sqlite3.Connection) -> None:
    """Add model_name column to track which embedding model was used."""
    conn.execute("""
ALTER TABLE embeddings ADD COLUMN model_name TEXT NOT NULL DEFAULT 'all-MiniLM-L6-v2'
""")


def _open_connection(db_path: Path) -> sqlite3.Connection:
    import shutil
    import logging
    try:
        conn = sqlite3.connect(str(db_path), timeout=10.0)
        conn.execute("PRAGMA integrity_check")  # Detect corruption
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn
    except sqlite3.DatabaseError as e:
        # DB is corrupted — back it up and start fresh
        backup_path = db_path.with_suffix(".corrupt.db")
        try:
            shutil.move(str(db_path), str(backup_path))
            logging.warning(f"Corrupted DB moved to {backup_path}, starting fresh")
        except OSError:
            db_path.unlink(missing_ok=True)
        # Retry with fresh DB
        conn = sqlite3.connect(str(db_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

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
        
        self._conn = _open_connection(db_path)

        result = self._conn.execute("PRAGMA journal_mode").fetchone()
        if result and result[0] != "wal":
            logging.warning("SQLite WAL mode unavailable (network filesystem?). Using default journal mode.")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.commit()

        # Create schema and run migrations
        self._ensure_schema()
        
        # Store schema version in meta as well for legacy compat
        self.set_meta("schema_version", str(CURRENT_SCHEMA_VERSION))

    def _ensure_schema(self) -> None:
        # Create schema_version table if it doesn't exist
        self._conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        )
        """)
        if not self._conn.execute("SELECT 1 FROM schema_version").fetchone():
            self._conn.execute("INSERT INTO schema_version VALUES (0)")
            
        current = self._conn.execute("SELECT version FROM schema_version").fetchone()[0]
        if current < CURRENT_SCHEMA_VERSION:
            for version in range(current + 1, CURRENT_SCHEMA_VERSION + 1):
                if version in MIGRATIONS:
                    MIGRATIONS[version](self._conn)
            self._conn.execute("UPDATE schema_version SET version = ?", (CURRENT_SCHEMA_VERSION,))
            self._conn.commit()

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

    def count_embeddings(self) -> int:
        """Count the total number of cached embeddings."""
        row = self._conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> EmbeddingDB:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
