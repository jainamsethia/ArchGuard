"""MiniLM embedding pipeline and semantic drift detection."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from archguard.cache.embeddings import EmbeddingCache


@dataclass
class FunctionChunk:
    """A single function extracted from source code."""

    file_path: str
    function_name: str
    source: str            # docstring + body combined
    content_hash: str      # SHA256 of source


@dataclass
class SemanticDriftResult:
    """Result of semantic drift analysis for one module."""

    module_name: str
    drift_score: float               # cosine distance 0.0–1.0
    pre_pr_centroid: npt.NDArray[np.float32]
    post_pr_centroid: npt.NDArray[np.float32]
    functions_analyzed: int
    cache_hit: bool


# ------------------------------------------------------------------
# Cosine helpers
# ------------------------------------------------------------------

def cosine_distance(a: npt.NDArray[np.float32], b: npt.NDArray[np.float32]) -> float:
    """``1 - cosine_similarity``, clamped to ``[0.0, 1.0]``."""
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0
    sim = float(np.dot(a, b) / (norm_a * norm_b))
    return float(np.clip(1.0 - sim, 0.0, 1.0))


# ------------------------------------------------------------------
# Analyzer
# ------------------------------------------------------------------

def extract_module_text(file_path: Path) -> str:
    """
    Extract meaningful text from a Python module for semantic analysis.
    Uses ast.get_docstring() for correct handling of all docstring styles
    (triple-quoted, r-prefixed, u-prefixed, indented, multi-line).
    """
    import ast
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except SyntaxError:
        return ""

    texts = []
    # Module docstring
    module_doc = ast.get_docstring(tree)
    if module_doc:
        texts.append(module_doc)

    # Class and function docstrings + names
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            texts.append(node.name)  # function/class name as semantic signal
            doc = ast.get_docstring(node)
            if doc:
                texts.append(doc)

    return " ".join(texts)

class SemanticAnalyzer:
    """Embedding pipeline + drift detection using all-MiniLM-L6-v2."""

    def __init__(self, cache: EmbeddingCache) -> None:
        self._cache: EmbeddingCache = cache

    def embed_chunks(
        self,
        chunks: list[FunctionChunk],
        batch_size: int = 32,
    ) -> dict[str, npt.NDArray[np.float32]]:
        """Embed function chunks, using cache where possible.

        Lazy-imports ``SentenceTransformer("all-MiniLM-L6-v2")``.
        Returns ``{"{file_path}::{function_name}": embedding}``.
        """
        result: dict[str, npt.NDArray[np.float32]] = {}
        to_embed: list[FunctionChunk] = []

        # Check cache first using batch get
        all_keys = [f"{c.file_path}::{c.function_name}::{c.content_hash}" for c in chunks]
        cached_batch = self._cache.get_batch(all_keys)

        for chunk, key in zip(chunks, all_keys):
            cached = cached_batch.get(key)
            result_key = f"{chunk.file_path}::{chunk.function_name}"
            if cached is not None:
                result[result_key] = cached
            else:
                to_embed.append(chunk)

        # Embed cache misses
        if to_embed:
            try:
                from sentence_transformers import SentenceTransformer  # lazy
                model = SentenceTransformer("all-MiniLM-L6-v2")
                texts = [c.source for c in to_embed]
                embeddings = model.encode(texts, batch_size=batch_size)
                model_name = "all-MiniLM-L6-v2"
            except ImportError:
                embeddings = [np.zeros(384, dtype=np.float32) for _ in to_embed]
                model_name = "none"

            new_items = {}
            for i, chunk in enumerate(to_embed):
                result_key = f"{chunk.file_path}::{chunk.function_name}"
                emb = np.array(embeddings[i], dtype=np.float32)
                result[result_key] = emb
                
                insert_key = f"{chunk.file_path}::{chunk.function_name}::{chunk.content_hash}::{model_name}"
                new_items[insert_key] = emb
                
            self._cache.set_batch(new_items)

        return result

    def compute_centroid(
        self,
        embeddings: dict[str, npt.NDArray[np.float32]],
    ) -> npt.NDArray[np.float32]:
        """Mean of all embeddings, normalized to unit length.

        Raises ``ValueError`` if *embeddings* is empty.
        """
        if not embeddings:
            from archguard.utils.errors import AnalysisError
            raise AnalysisError("Cannot compute centroid of empty embeddings")

        matrix = np.array(list(embeddings.values()), dtype=np.float32)
        centroid: npt.NDArray[np.float32] = np.mean(matrix, axis=0)
        norm = float(np.linalg.norm(centroid))
        if norm > 0.0:
            centroid = centroid / norm
        return centroid

    def compute_drift(
        self,
        module_name: str,
        changed_files: list[Path],
        repo_root: Path,
    ) -> SemanticDriftResult:
        """Compute semantic drift for *module_name* given changed files."""
        # 1. Load pre-PR centroid
        cached = self._cache.get_centroid(module_name)
        cache_hit = cached is not None
        pre_centroid: npt.NDArray[np.float32]
        if cached is not None:
            pre_centroid = cached[0]
        else:
            pre_centroid = np.zeros(384, dtype=np.float32)

        # 2. Extract + embed functions from changed files
        all_embeddings: dict[str, npt.NDArray[np.float32]] = {}
        for fpath in changed_files:
            try:
                rel = str(fpath.relative_to(repo_root)).replace("\\", "/")
                source = extract_module_text(fpath)
                if not source:
                    continue
                content_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
                chunk = FunctionChunk(
                    file_path=rel,
                    function_name="<module>",
                    source=source,
                    content_hash=content_hash,
                )
                embedded = self.embed_chunks([chunk])
                all_embeddings.update(embedded)
            except Exception:  # noqa: BLE001
                continue

        # 3. Compute post-PR centroid
        if not all_embeddings:
            return SemanticDriftResult(
                module_name=module_name,
                drift_score=0.0,
                pre_pr_centroid=pre_centroid,
                post_pr_centroid=pre_centroid,
                functions_analyzed=0,
                cache_hit=cache_hit,
            )

        post_centroid = self.compute_centroid(all_embeddings)

        # 4. Compute drift
        if not cache_hit:
            drift = 0.0
        else:
            drift = cosine_distance(pre_centroid, post_centroid)

        # 5. Update stored centroid
        centroid_hash = hashlib.sha256(
            post_centroid.astype(np.float32).tobytes(),
        ).hexdigest()
        self._cache.store_centroid(module_name, post_centroid, centroid_hash)

        return SemanticDriftResult(
            module_name=module_name,
            drift_score=drift,
            pre_pr_centroid=pre_centroid,
            post_pr_centroid=post_centroid,
            functions_analyzed=len(all_embeddings),
            cache_hit=cache_hit,
        )
