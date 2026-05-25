"""Embedding cache read/write against EmbeddingDB."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import numpy as np
import numpy.typing as npt

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

    def get_all_embeddings(self) -> dict[str, tuple[npt.NDArray[np.float32], str]]:
        """Return all cached embeddings.

        Keys are ``"{file_path}::{function_name}"``.
        Values are ``(embedding_array, content_hash)``.
        """
        result: dict[str, tuple[npt.NDArray[np.float32], str]] = {}
        try:
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
