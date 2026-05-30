"""Unit tests for archguard.analysis.duplication."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from archguard.analysis.duplication import (
    DuplicationAnalyzer,
    duplication_score,
)
from archguard.cache.db import EmbeddingDB
from archguard.cache.embeddings import EmbeddingCache


@pytest.fixture()
def cache(tmp_path: Path) -> EmbeddingCache:
    db = EmbeddingDB(tmp_path / "test.db")
    return EmbeddingCache(db)


@pytest.fixture()
def analyzer(cache: EmbeddingCache) -> DuplicationAnalyzer:
    return DuplicationAnalyzer(cache)


class TestL2ToCosine:
    """Tests for _l2_to_cosine."""

    def test_zero_distance(self, analyzer: DuplicationAnalyzer) -> None:
        """L2=0.0 (identical) -> cosine_sim=1.0."""
        result = analyzer._l2_to_cosine(np.array([0.0]))
        assert float(result[0]) == pytest.approx(1.0)

    def test_max_distance(self, analyzer: DuplicationAnalyzer) -> None:
        """L2=2.0 (orthogonal unit vectors) -> cosine_sim=0.0."""
        result = analyzer._l2_to_cosine(np.array([2.0]))
        assert float(result[0]) == pytest.approx(0.0)


class TestDuplicationScore:
    """Tests for duplication_score."""

    def test_below_threshold(self) -> None:
        assert duplication_score(0.84) == 0.0

    def test_at_threshold(self) -> None:
        assert duplication_score(0.85) == pytest.approx(0.0, abs=1e-9)

    def test_at_max(self) -> None:
        assert duplication_score(1.00) == pytest.approx(1.0, abs=1e-9)

    def test_midpoint(self) -> None:
        assert duplication_score(0.925) == pytest.approx(0.5, abs=1e-6)


class TestDuplicationAnalyzer:
    """Tests for DuplicationAnalyzer.analyze_module."""

    def test_stale_cache_skips(
        self,
        cache: EmbeddingCache,
        tmp_path: Path,
    ) -> None:
        """Stale cache -> DuplicationResult(skipped=True)."""
        # Store centroid with old timestamp
        c = np.random.rand(384).astype(np.float32)
        cache.store_centroid("mod", c, "h")
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        cache._db._conn.execute(
            "UPDATE module_centroids SET updated_at = ? WHERE module_name = ?",
            (old, "mod"),
        )
        cache._db._conn.commit()

        analyzer = DuplicationAnalyzer(cache)
        result = analyzer.analyze_module("mod", ["a.py"])
        assert result.skipped is True
        assert "stale" in result.skip_reason.lower()

    def test_invalid_cache_no_skip(
        self,
        cache: EmbeddingCache,
    ) -> None:
        """Invalid cache (hash mismatch) -> skipped=False."""
        # No centroid stored -> is_cache_stale returns False
        analyzer = DuplicationAnalyzer(cache)
        result = analyzer.analyze_module("mod", ["a.py"])
        assert result.skipped is False

    def test_empty_corpus(
        self,
        cache: EmbeddingCache,
    ) -> None:
        """Empty corpus -> aggregate_score=0.0, no matches."""
        analyzer = DuplicationAnalyzer(cache)
        result = analyzer.analyze_module("mod", ["a.py"])
        assert result.aggregate_score == 0.0
        assert result.matches == []

    def test_same_module_excluded(
        self,
        cache: EmbeddingCache,
    ) -> None:
        """Same-module matches are excluded."""
        # Store two embeddings from the same module and one from another
        v = np.ones(384, dtype=np.float32)
        v = v / np.linalg.norm(v)
        cache.store_embedding("a.py", "f1", v, "h1", "m")
        cache.store_embedding("a.py", "f2", v, "h2", "m")  # same module
        cache.store_embedding("b.py", "f3", v, "h3", "m")  # different module

        # Mock faiss
        mock_faiss = MagicMock()
        mock_index = MagicMock()
        # For 3 embeddings, search returns self + 2 neighbors
        # Return indices [0,1,2] with distances [0,0,0] (identical)
        mock_index.search.return_value = (
            np.array([[0.0, 0.0, 0.0]], dtype=np.float32),
            np.array([[0, 1, 2]], dtype=np.int64),
        )
        mock_faiss.IndexFlatL2.return_value = mock_index

        analyzer = DuplicationAnalyzer(cache)

        with patch.dict(sys.modules, {"faiss": mock_faiss}):
            result = analyzer.analyze_module(
                "mod",
                ["a.py"],
                k=10,
            )

        # Only matches with b.py should appear (cross-module)
        for m in result.matches:
            assert not m.matched_function.startswith("a.py::")

    def test_same_function_name_different_files_detected(
        self,
        cache: EmbeddingCache,
    ) -> None:
        """Two files with the same function name are detected as duplicates."""
        v1 = np.ones(384, dtype=np.float32)
        v1 = v1 / np.linalg.norm(v1)

        # Store embeddings for a.py::process and b.py::process
        cache.store_embedding("a.py", "process", v1, "h1", "m")
        cache.store_embedding("b.py", "process", v1, "h2", "m")

        # Mock faiss
        mock_faiss = MagicMock()
        mock_index = MagicMock()
        # Ensure searching for a.py::process (index 0) returns itself and b.py::process (index 1)
        mock_index.search.return_value = (
            np.array([[0.0, 0.0]], dtype=np.float32),
            np.array([[0, 1]], dtype=np.int64),
        )
        mock_faiss.IndexFlatL2.return_value = mock_index

        analyzer = DuplicationAnalyzer(cache)
        with patch.dict(sys.modules, {"faiss": mock_faiss}):
            result = analyzer.analyze_module("mod", ["a.py"], k=10)

        assert len(result.matches) > 0
        match = result.matches[0]
        assert match.source_function == "a.py::process"
        assert match.matched_function == "b.py::process"
        assert result.aggregate_score <= 1.0
