"""Unit tests for archguard.analysis.semantic."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from archguard.analysis.semantic import (
    SemanticAnalyzer,
    FunctionChunk,
    cosine_distance,
    extract_module_text,
)
from archguard.cache.db import EmbeddingDB
from archguard.cache.embeddings import EmbeddingCache


TWO_FUNC_SOURCE = '''
def foo(x):
    """Docstring for foo."""
    a = x + 1
    b = a * 2
    return b

def bar(y):
    c = y - 1
    d = c / 2
    return d
'''

SHORT_FUNC_SOURCE = '''
def tiny(x):
    return x
'''

DOCSTRING_SOURCE = '''
def documented(x):
    """This is a docstring."""
    a = x + 1
    b = a * 2
    return b
'''


@pytest.fixture()
def cache(tmp_path: Path) -> EmbeddingCache:
    db = EmbeddingDB(tmp_path / "test.db")
    return EmbeddingCache(db)


@pytest.fixture()
def analyzer(cache: EmbeddingCache) -> SemanticAnalyzer:
    return SemanticAnalyzer(cache)


class TestExtractModuleText:
    """Tests for extract_module_text."""

    def test_r_string_docstring(self, tmp_path: Path) -> None:
        src = 'r"""This is an r-string docstring"""\ndef foo(): pass'
        f = tmp_path / "test.py"
        f.write_text(src, encoding="utf-8")
        text = extract_module_text(f)
        assert "This is an r-string docstring" in text
        assert "foo" in text

    def test_class_docstring(self, tmp_path: Path) -> None:
        src = 'class MyClass:\n    """Class docstring."""\n    pass'
        f = tmp_path / "test.py"
        f.write_text(src, encoding="utf-8")
        text = extract_module_text(f)
        assert "Class docstring" in text
        assert "MyClass" in text

    def test_no_docstring(self, tmp_path: Path) -> None:
        src = 'def foo(): pass\nclass Bar: pass'
        f = tmp_path / "test.py"
        f.write_text(src, encoding="utf-8")
        text = extract_module_text(f)
        assert "foo" in text
        assert "Bar" in text

    def test_syntax_error(self, tmp_path: Path) -> None:
        src = 'def foo(:::'
        f = tmp_path / "test.py"
        f.write_text(src, encoding="utf-8")
        text = extract_module_text(f)
        assert text == ""


class TestComputeCentroid:
    """Tests for SemanticAnalyzer.compute_centroid."""

    def test_identical_embeddings(
        self, analyzer: SemanticAnalyzer,
    ) -> None:
        v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        result = analyzer.compute_centroid({"a": v, "b": v})
        norm = np.linalg.norm(result)
        assert abs(norm - 1.0) < 1e-5  # unit-normalized

    def test_empty_raises(self, analyzer: SemanticAnalyzer) -> None:
        from archguard.utils.errors import AnalysisError
        with pytest.raises(AnalysisError, match="empty"):
            analyzer.compute_centroid({})


class TestCosineDistance:
    """Tests for cosine_distance."""

    def test_identical_vectors(self) -> None:
        a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        assert cosine_distance(a, a) == pytest.approx(0.0, abs=1e-6)

    def test_opposite_vectors(self) -> None:
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([-1.0, 0.0], dtype=np.float32)
        assert cosine_distance(a, b) == pytest.approx(1.0, abs=1e-6)

    def test_zero_vector(self) -> None:
        a = np.zeros(3, dtype=np.float32)
        b = np.ones(3, dtype=np.float32)
        assert cosine_distance(a, b) == 1.0


class TestComputeDrift:
    """Tests for SemanticAnalyzer.compute_drift."""

    def test_no_stored_centroid(
        self, analyzer: SemanticAnalyzer, tmp_path: Path,
    ) -> None:
        """No stored centroid -> drift_score=0.0, cache_hit=False."""
        py_file = tmp_path / "m.py"
        py_file.write_text(TWO_FUNC_SOURCE, encoding="utf-8")

        mock_st = MagicMock()
        mock_model = MagicMock()
        mock_model.encode.side_effect = (
            lambda texts, **kw: np.random.rand(len(texts), 384).astype(np.float32)
        )
        mock_st.SentenceTransformer.return_value = mock_model

        with patch.dict(sys.modules, {"sentence_transformers": mock_st}):
            result = analyzer.compute_drift("mod", [py_file], tmp_path)

        assert result.drift_score == 0.0
        assert result.cache_hit is False

    def test_identical_embeddings_zero_drift(
        self, cache: EmbeddingCache, tmp_path: Path,
    ) -> None:
        """Identical pre/post embeddings -> drift_score=0.0."""
        # Store a known centroid
        fixed = np.ones(384, dtype=np.float32)
        fixed = fixed / np.linalg.norm(fixed)  # unit normalize
        cache.store_centroid("mod", fixed, "hash")

        py_file = tmp_path / "m.py"
        py_file.write_text(TWO_FUNC_SOURCE, encoding="utf-8")

        # Mock encoder to return the same fixed vector for all inputs
        mock_st = MagicMock()
        mock_model = MagicMock()
        mock_model.encode.side_effect = (
            lambda texts, **kw: np.tile(fixed, (len(texts), 1))
        )
        mock_st.SentenceTransformer.return_value = mock_model

        analyzer = SemanticAnalyzer(cache)
        with patch.dict(sys.modules, {"sentence_transformers": mock_st}):
            result = analyzer.compute_drift("mod", [py_file], tmp_path)

        assert result.drift_score == pytest.approx(0.0, abs=1e-5)
        assert result.cache_hit is True
