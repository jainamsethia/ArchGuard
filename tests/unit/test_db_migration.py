import sqlite3
from archguard.cache.db import EmbeddingDB


def test_migration_runs_on_old_schema(tmp_path):
    db_path = tmp_path / "test.db"

    # Create old schema manually
    conn = sqlite3.connect(db_path)
    # The actual V1 schema structure (matching _migrate_v1)
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
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version VALUES (1)")
    conn.commit()
    conn.close()

    # Opening with new code should run migration
    with EmbeddingDB(db_path):
        pass  # Should not raise

    # Verify new column exists
    conn = sqlite3.connect(db_path)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(embeddings)")]
    assert "model_name" in cols

    # Verify version was updated
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == 2
    conn.close()


def test_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    # Running migration twice should not fail
    with EmbeddingDB(db_path):
        pass
    with EmbeddingDB(db_path):
        pass  # Should not raise
