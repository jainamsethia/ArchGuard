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

class SemanticAnalyzer:
    """Embedding pipeline + drift detection using all-MiniLM-L6-v2."""

    def __init__(self, cache: EmbeddingCache) -> None:
        self._cache: EmbeddingCache = cache

    def extract_functions(
        self,
        source: str,
        file_path: str,
    ) -> list[FunctionChunk]:
        """Extract function definitions from *source* via tree-sitter.

        Skips functions whose body has fewer than 3 lines.
        Includes docstring (if present) in the chunk source.
        """
        from tree_sitter import Language, Parser  # lazy import
        import tree_sitter_python as tspython  # lazy import

        parser: Any = Parser(Language(tspython.language()))
        tree: Any = parser.parse(source.encode("utf-8"))

        chunks: list[FunctionChunk] = []
        stack: list[Any] = [tree.root_node]

        while stack:
            node = stack.pop()
            if node.type == "function_definition":
                name_node = node.child_by_field_name("name")
                body_node = node.child_by_field_name("body")
                if name_node is None or body_node is None:
                    continue

                func_name: str = name_node.text.decode("utf-8")
                body_text: str = body_node.text.decode("utf-8")

                # Count body lines (skip if < 3)
                body_lines = [l for l in body_text.splitlines() if l.strip()]
                if len(body_lines) < 3:
                    stack.extend(reversed(node.children))
                    continue

                # Extract docstring if present
                docstring = ""
                if body_node.children:
                    first_child = body_node.children[0]
                    if first_child.type == "expression_statement":
                        for sub in first_child.children:
                            if sub.type == "string":
                                docstring = sub.text.decode("utf-8")
                                break

                combined = f"{docstring}\n{body_text}" if docstring else body_text
                content_hash = hashlib.sha256(
                    combined.encode("utf-8"),
                ).hexdigest()

                chunks.append(FunctionChunk(
                    file_path=file_path,
                    function_name=func_name,
                    source=combined,
                    content_hash=content_hash,
                ))

            # Continue walking children
            stack.extend(reversed(node.children))

        return chunks

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

        # Check cache first
        for chunk in chunks:
            key = f"{chunk.file_path}::{chunk.function_name}"
            cached = self._cache.get_embedding(
                chunk.file_path, chunk.function_name, chunk.content_hash,
            )
            if cached is not None:
                result[key] = cached
            else:
                to_embed.append(chunk)

        # Embed cache misses
        if to_embed:
            from sentence_transformers import SentenceTransformer  # lazy

            model = SentenceTransformer("all-MiniLM-L6-v2")
            texts = [c.source for c in to_embed]
            embeddings = model.encode(texts, batch_size=batch_size)

            for i, chunk in enumerate(to_embed):
                key = f"{chunk.file_path}::{chunk.function_name}"
                emb = np.array(embeddings[i], dtype=np.float32)
                result[key] = emb
                self._cache.store_embedding(
                    chunk.file_path,
                    chunk.function_name,
                    emb,
                    chunk.content_hash,
                    "all-MiniLM-L6-v2",
                )

        return result

    def compute_centroid(
        self,
        embeddings: dict[str, npt.NDArray[np.float32]],
    ) -> npt.NDArray[np.float32]:
        """Mean of all embeddings, normalized to unit length.

        Raises ``ValueError`` if *embeddings* is empty.
        """
        if not embeddings:
            raise ValueError("Cannot compute centroid of empty embeddings")

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
                source = fpath.read_text(errors="replace")
                rel = str(fpath.relative_to(repo_root)).replace("\\", "/")
                chunks = self.extract_functions(source, rel)
                embedded = self.embed_chunks(chunks)
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
