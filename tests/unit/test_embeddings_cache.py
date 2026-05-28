"""Unit tests for archguard.cache.embeddings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from archguard.cache.db import EmbeddingDB
from archguard.cache.embeddings import EmbeddingCache, sha256_of


@pytest.fixture()
def cache(tmp_path: Path) -> EmbeddingCache:
    """Create a fresh EmbeddingCache backed by a temp DB."""
    db = EmbeddingDB(tmp_path / "test.db")
    return EmbeddingCache(db)


class TestEmbeddingCache:
    """Tests for EmbeddingCache."""

    def test_store_get_roundtrip(self, cache: EmbeddingCache) -> None:
        """store then get with same hash -> returns array."""
        emb = np.random.rand(384).astype(np.float32)
        cache.store_embedding("a.py", "foo", emb, "hash1", "model")
        result = cache.get_embedding("a.py", "foo", "hash1")
        assert result is not None
        np.testing.assert_array_almost_equal(result, emb)

    def test_get_different_hash_returns_none(
        self, cache: EmbeddingCache,
    ) -> None:
        """get with different hash -> None (stale)."""
        emb = np.random.rand(384).astype(np.float32)
        cache.store_embedding("a.py", "foo", emb, "hash1", "model")
        assert cache.get_embedding("a.py", "foo", "hash2") is None

    def test_get_missing_returns_none(
        self, cache: EmbeddingCache,
    ) -> None:
        """get for non-existent key -> None."""
        assert cache.get_embedding("nope.py", "bar", "x") is None

    def test_centroid_roundtrip(self, cache: EmbeddingCache) -> None:
        """store_centroid then get_centroid round-trip."""
        c = np.random.rand(384).astype(np.float32)
        cache.store_centroid("mod_a", c, "chash")
        result = cache.get_centroid("mod_a")
        assert result is not None
        arr, h = result
        np.testing.assert_array_almost_equal(arr, c)
        assert h == "chash"

    def test_is_cache_valid_true(self, cache: EmbeddingCache) -> None:
        """is_cache_valid -> True when hashes match."""
        c = np.random.rand(384).astype(np.float32)
        cache.store_centroid("mod", c, "abc")
        assert cache.is_cache_valid("mod", "abc") is True

    def test_is_cache_valid_false(self, cache: EmbeddingCache) -> None:
        """is_cache_valid -> False when hashes differ."""
        c = np.random.rand(384).astype(np.float32)
        cache.store_centroid("mod", c, "abc")
        assert cache.is_cache_valid("mod", "xyz") is False

    def test_is_cache_stale_recent(self, cache: EmbeddingCache) -> None:
        """is_cache_stale -> False when updated 1 hour ago."""
        c = np.random.rand(384).astype(np.float32)
        cache.store_centroid("mod", c, "h")
        assert cache.is_cache_stale("mod", max_age_hours=24) is False

    def test_is_cache_stale_old(self, cache: EmbeddingCache) -> None:
        """is_cache_stale -> True when updated_at is 25 hours ago."""
        c = np.random.rand(384).astype(np.float32)
        cache.store_centroid("mod", c, "h")
        # Manually backdate the updated_at
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        cache._db._conn.execute(
            "UPDATE module_centroids SET updated_at = ? WHERE module_name = ?",
            (old_time, "mod"),
        )
        cache._db._conn.commit()
        assert cache.is_cache_stale("mod", max_age_hours=24) is True

    def test_get_all_embeddings(self, cache: EmbeddingCache) -> None:
        """get_all_embeddings returns all stored entries."""
        e1 = np.random.rand(384).astype(np.float32)
        e2 = np.random.rand(384).astype(np.float32)
        cache.store_embedding("a.py", "f1", e1, "h1", "m")
        cache.store_embedding("b.py", "f2", e2, "h2", "m")
        result = cache.get_all_embeddings()
        assert "a.py::f1" in result
        assert "b.py::f2" in result

    def test_concurrent_writes(self, tmp_path: Path) -> None:
        """Two EmbeddingCache instances writing -> no corruption (WAL)."""
        db1 = EmbeddingDB(tmp_path / "shared.db")
        db2 = EmbeddingDB(tmp_path / "shared.db")
        c1 = EmbeddingCache(db1)
        c2 = EmbeddingCache(db2)

        e1 = np.random.rand(384).astype(np.float32)
        e2 = np.random.rand(384).astype(np.float32)
        c1.store_embedding("x.py", "a", e1, "h1", "m")
        c2.store_embedding("y.py", "b", e2, "h2", "m")

        all_1 = c1.get_all_embeddings()
        assert "x.py::a" in all_1
        # c2's write should also be visible via WAL
        db1.close()
        db2.close()

    def test_get_batch_single_query(self, cache: EmbeddingCache) -> None:
        """get_batch should execute exactly one query."""
        e1 = np.random.rand(384).astype(np.float32)
        e2 = np.random.rand(384).astype(np.float32)
        cache.store_embedding("a.py", "f1", e1, "h1", "m")
        cache.store_embedding("b.py", "f2", e2, "h2", "m")

        queries = []
        def trace(stmt):
            if stmt.strip().upper().startswith("SELECT"):
                queries.append(stmt)
                
        cache._db._conn.set_trace_callback(trace)

        keys = ["a.py::f1::h1", "b.py::f2::h2", "missing.py::f3::h3"]
        result = cache.get_batch(keys)
        
        cache._db._conn.set_trace_callback(None)
        
        assert len(result) == 3
        assert result["a.py::f1::h1"] is not None
        assert result["b.py::f2::h2"] is not None
        assert result.get("missing.py::f3::h3") is None
        
        # Exactly one SELECT query
        assert len(queries) == 1
