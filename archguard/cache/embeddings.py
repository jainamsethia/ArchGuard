"""Embedding cache read/write against EmbeddingDB."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

try:
    import numpy as np
    import numpy.typing as npt
    _ML_AVAILABLE = True
except ImportError:
    _ML_AVAILABLE = False
    np = None  # type: ignore[assignment]
    npt = None  # type: ignore[assignment]

from archguard.cache.db import EmbeddingDB

logger: logging.Logger = logging.getLogger(__name__)


def sha256_of(text: str) -> str:
    """Compute SHA-256 hex digest of *text*."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EmbeddingCache:
    """High-level cache for embeddings and module centroids.

    Never raises on DB failures — logs warnings and returns ``None``.
    """

    def __init__(self, db: EmbeddingDB) -> None:
        self._db: EmbeddingDB = db

    # ----------------------------------------------------------
    # Embeddings
    # ----------------------------------------------------------

    def get_embedding(
        self,
        file_path: str,
        function_name: str,
        content_hash: str,
    ) -> npt.NDArray[np.float32] | None:
        """Return stored embedding if *content_hash* matches, else ``None``."""
        if not _ML_AVAILABLE:
            raise RuntimeError(
                "ML dependencies are not installed. Run: pip install archguard[ml]"
            )
        try:
            cursor = self._db._conn.execute(
                "SELECT embedding, content_hash FROM embeddings "
                "WHERE file_path = ? AND function_name = ?",
                (file_path, function_name),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            stored_blob, stored_hash = row
            if stored_hash != content_hash:
                return None  # stale
            return np.frombuffer(stored_blob, dtype=np.float32).copy()
        except Exception:  # noqa: BLE001
            logger.warning("Failed to read embedding for %s::%s", file_path, function_name)
            return None

    def store_embedding(
        self,
        file_path: str,
        function_name: str,
        embedding: npt.NDArray[np.float32],
        content_hash: str,
        model_name: str,
    ) -> None:
        """Upsert an embedding into the cache."""
        if not _ML_AVAILABLE:
            raise RuntimeError(
                "ML dependencies are not installed. Run: pip install archguard[ml]"
            )
        try:
            blob = embedding.astype(np.float32).tobytes()
            now = datetime.now(timezone.utc).isoformat()
            self._db._conn.execute(
                "INSERT OR REPLACE INTO embeddings "
                "(file_path, function_name, embedding, content_hash, model_name, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (file_path, function_name, blob, content_hash, model_name, now),
            )
            self._db._conn.commit()
        except Exception:  # noqa: BLE001
            logger.warning("Failed to store embedding for %s::%s", file_path, function_name)

    # ----------------------------------------------------------
    # Centroids
    # ----------------------------------------------------------

    def get_centroid(self, module_name: str) -> tuple[npt.NDArray[np.float32], str] | None:
        """Return ``(centroid_array, content_hash)`` or ``None``."""
        if not _ML_AVAILABLE:
            raise RuntimeError(
                "ML dependencies are not installed. Run: pip install archguard[ml]"
            )
        try:
            cursor = self._db._conn.execute(
                "SELECT centroid, content_hash FROM module_centroids "
                "WHERE module_name = ?",
                (module_name,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            blob, content_hash = row
            return np.frombuffer(blob, dtype=np.float32).copy(), content_hash
        except Exception:  # noqa: BLE001
            logger.warning("Failed to read centroid for %s", module_name)
            return None

    def store_centroid(
        self,
        module_name: str,
        centroid: npt.NDArray[np.float32],
        content_hash: str,
    ) -> None:
        """Upsert a module centroid."""
        if not _ML_AVAILABLE:
            raise RuntimeError(
                "ML dependencies are not installed. Run: pip install archguard[ml]"
            )
        try:
            blob = centroid.astype(np.float32).tobytes()
            now = datetime.now(timezone.utc).isoformat()
            self._db._conn.execute(
                "INSERT OR REPLACE INTO module_centroids "
                "(module_name, centroid, content_hash, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (module_name, blob, content_hash, now),
            )
            self._db._conn.commit()
        except Exception:  # noqa: BLE001
            logger.warning("Failed to store centroid for %s", module_name)

    # ----------------------------------------------------------
    # Bulk reads
    # ----------------------------------------------------------

    def get_batch(self, keys: list[str]) -> dict[str, npt.NDArray[np.float32] | None]:
        """
        Batch retrieve embeddings. Write lock is acquired for writes; reads use SQLite WAL which allows concurrent readers.
        keys format: "file_path::function_name::content_hash"
        """
        if not _ML_AVAILABLE:
            raise RuntimeError(
                "ML dependencies are not installed. Run: pip install archguard[ml]"
            )
        if not keys:
            return {}

        result: dict[str, npt.NDArray[np.float32] | None] = {k: None for k in keys}
        
        lookup_keys = []
        hash_map = {}
        for k in keys:
            parts = k.split("::")
            if len(parts) >= 3:
                fp_fn = f"{parts[0]}::{parts[1]}"
                chash = parts[2]
                lookup_keys.append(fp_fn)
                hash_map[fp_fn] = chash

        if not lookup_keys:
            return result

        try:
            placeholders = ",".join("?" * len(lookup_keys))
            query = (
                f"SELECT file_path || '::' || function_name, content_hash, embedding "
                f"FROM embeddings "
                f"WHERE file_path || '::' || function_name IN ({placeholders})"
            )
            cursor = self._db._conn.execute(query, lookup_keys)
            
            for row in cursor:
                fp_fn, chash, blob = row
                expected_hash = hash_map.get(fp_fn)
                if expected_hash == chash:
                    key = f"{fp_fn}::{chash}"
                    if key in result:
                        result[key] = np.frombuffer(blob, dtype=np.float32).copy()
        except Exception:
            logger.warning("Failed to get_batch")

        return result

    def set_batch(self, items: dict[str, npt.NDArray[np.float32]]) -> None:
        """
        Batch insert embeddings.
        Write lock is acquired for writes; reads use SQLite WAL which allows concurrent readers.
        items format: "file_path::function_name::content_hash::model_name" -> embedding
        """
        if not _ML_AVAILABLE:
            raise RuntimeError(
                "ML dependencies are not installed. Run: pip install archguard[ml]"
            )
        if not items:
            return

        from archguard.cache.locking import file_lock
        from pathlib import Path
        db_file = self._db._conn.execute("PRAGMA database_list").fetchall()[0][2]
        lock_path = Path(db_file).with_suffix(".lock")

        rows = []
        now = datetime.now(timezone.utc).isoformat()
        for k, emb in items.items():
            parts = k.split("::")
            if len(parts) >= 4:
                fp = parts[0]
                fn = parts[1]
                chash = parts[2]
                model = parts[3]
                blob = emb.astype(np.float32).tobytes()
                rows.append((fp, fn, blob, chash, model, now))

        if not rows:
            return

        try:
            with file_lock(lock_path):
                self._db._conn.executemany(
                    "INSERT OR REPLACE INTO embeddings "
                    "(file_path, function_name, embedding, content_hash, model_name, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    rows
                )
                self._db._conn.commit()
        except Exception:
            logger.warning("Failed to set_batch")

    def get_all_embeddings(self) -> dict[str, tuple[npt.NDArray[np.float32], str]]:
        """Return all cached embeddings.

        Keys are ``"{file_path}::{function_name}"``.
        Values are ``(embedding_array, content_hash)``.
        """
        if not _ML_AVAILABLE:
            raise RuntimeError(
                "ML dependencies are not installed. Run: pip install archguard[ml]"
            )
        from archguard.cache.locking import file_lock
        from pathlib import Path
        db_file = self._db._conn.execute("PRAGMA database_list").fetchall()[0][2]
        lock_path = Path(db_file).with_suffix(".lock")

        result: dict[str, tuple[npt.NDArray[np.float32], str]] = {}
        try:
            with file_lock(lock_path):
                cursor = self._db._conn.execute(
                    "SELECT file_path, function_name, embedding, content_hash "
                    "FROM embeddings",
                )
                for row in cursor:
                    fp, fn, blob, chash = row
                    key = f"{fp}::{fn}"
                    arr = np.frombuffer(blob, dtype=np.float32).copy()
                    result[key] = (arr, chash)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to read all embeddings")
        return result

    # ----------------------------------------------------------
    # Validity / staleness checks
    # ----------------------------------------------------------

    def is_cache_valid(self, module_name: str, current_hash: str) -> bool:
        """Return ``True`` if stored centroid hash equals *current_hash*."""
        result = self.get_centroid(module_name)
        if result is None:
            return False
        _, stored_hash = result
        return stored_hash == current_hash

    def is_cache_stale(
        self,
        module_name: str,
        max_age_hours: int = 24,
    ) -> bool:
        """Return ``True`` if centroid is older than *max_age_hours*.

        CI-RQ-02: staleness = time-expired AND hash still matches.
        """
        try:
            cursor = self._db._conn.execute(
                "SELECT updated_at FROM module_centroids "
                "WHERE module_name = ?",
                (module_name,),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            updated_str: str = row[0]
            updated_at = datetime.fromisoformat(updated_str)
            age = datetime.now(timezone.utc) - updated_at
            return age.total_seconds() > max_age_hours * 3600
        except Exception:  # noqa: BLE001
            logger.warning("Failed to check staleness for %s", module_name)
            return False
