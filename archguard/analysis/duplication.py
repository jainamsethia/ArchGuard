"""FAISS similarity search and duplication scoring."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import typing

try:
    import numpy as np
    import numpy.typing as npt

    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False
    np: typing.Any = None  # type: ignore[no-redef]
    npt: typing.Any = None  # type: ignore[no-redef]

try:
    import faiss

    _ML_AVAILABLE = _NUMPY_AVAILABLE
except ImportError:
    _ML_AVAILABLE = False
    faiss: typing.Any = None  # type: ignore[no-redef]

from archguard.audit.logger import AuditLogger
from archguard.cache.embeddings import EmbeddingCache
from archguard.config import EVENT_DUPLICATION_SKIPPED

logger: logging.Logger = logging.getLogger(__name__)


def duplication_score(similarity: float) -> float:
    """Compute duplication score from cosine similarity.

    Linear interpolation between 0.85 and 1.0.
    ``similarity < 0.85`` -> ``0.0``.
    """
    return min(1.0, max(0.0, (similarity - 0.85) / 0.15))


@dataclass
class DuplicationMatch:
    """A single cross-module duplication match."""

    source_function: str  # "file_path::function_name"
    matched_function: str  # "file_path::function_name"
    similarity: float  # cosine similarity 0.0–1.0
    duplication_score: float  # max(0, (similarity - 0.85) / 0.15)


@dataclass
class DuplicationResult:
    """Duplication analysis result for one module."""

    module_name: str
    matches: list[DuplicationMatch] = field(default_factory=list)
    aggregate_score: float = 0.0
    skipped: bool = False
    skip_reason: str = ""


class DuplicationAnalyzer:
    """Cross-module duplication detection using FAISS."""

    def __init__(
        self,
        cache: EmbeddingCache,
        audit: AuditLogger | None = None,
    ) -> None:
        self._cache: EmbeddingCache = cache
        self._audit: AuditLogger = audit or AuditLogger()

    def build_index(
        self,
        embeddings: dict[str, npt.NDArray[np.float32]],
    ) -> tuple[Any, list[str]]:
        """Build a FAISS ``IndexFlatL2`` from *embeddings*.

        All embeddings are unit-normalized before adding.
        Returns ``(index, ordered_keys)``.
        """
        if not _ML_AVAILABLE:
            raise RuntimeError(
                "ML dependencies are not installed. Run: pip install -e \".[ml]\""
            )

        keys = list(embeddings.keys())
        if not keys:
            index = faiss.IndexFlatL2(384)
            return index, keys

        matrix = np.array(
            [embeddings[k] for k in keys],
            dtype=np.float32,
        )

        # Unit-normalize
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        matrix = matrix / norms

        dim = matrix.shape[1]
        index = faiss.IndexFlatL2(dim)
        index.add(matrix)
        return index, keys

    def _l2_to_cosine(
        self, l2_distances: npt.NDArray[np.float32]
    ) -> npt.NDArray[np.float32]:
        """Convert FAISS L2 distances to cosine similarities.

        For unit-normalized vectors: ``cosine_sim = 1 - (L2^2 / 2)``.
        Clamped to ``[0.0, 1.0]``.
        """
        if not _ML_AVAILABLE:
            raise RuntimeError(
                "ML dependencies are not installed. Run: pip install -e \".[ml]\""
            )
        return np.clip(1.0 - (l2_distances**2) / 2.0, 0.0, 1.0)

    def _build_faiss_index(
        self, module_file_set: set[str]
    ) -> tuple[Any, list[str], dict[str, npt.NDArray[np.float32]]]:
        from archguard.config import EMBEDDING_BATCH_SIZE

        index = None
        keys: list[str] = []
        module_embeddings: dict[str, npt.NDArray[np.float32]] = {}

        for batch in self._cache.iter_embeddings(batch_size=EMBEDDING_BATCH_SIZE):
            if not batch:
                continue

            batch_paths = [p for p, _ in batch]

            for p, v in batch:
                file_part = p.split("::")[0]
                if file_part in module_file_set:
                    module_embeddings[p] = v

            batch_vecs = np.vstack([v for _, v in batch])
            norms = np.linalg.norm(batch_vecs, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            batch_vecs = batch_vecs / norms

            if index is None:
                dim = batch_vecs.shape[1]
                if faiss is None:
                    raise RuntimeError(
                        "faiss-cpu is required for duplication analysis. "
                        "Install with: pip install -e \".[ml]\""
                    )
                index = faiss.IndexFlatL2(dim)

            index.add(batch_vecs)
            keys.extend(batch_paths)

        return index, keys, module_embeddings

    def _query_module_matches(
        self,
        index: Any,
        keys: list[str],
        module_embeddings: dict[str, npt.NDArray[np.float32]],
        module_name: str,
        module_paths_cfg: list[str],
        k: int,
    ) -> list[DuplicationMatch]:
        
        
        matches: list[DuplicationMatch] = []
        for func_key, emb in module_embeddings.items():
            query = emb.astype(np.float32).reshape(1, -1)
            qnorm = np.linalg.norm(query)
            if qnorm > 0:
                query = query / qnorm

            actual_k = min(k + 1, len(keys))
            distances, indices = index.search(query, actual_k)
            cosine_sims = self._l2_to_cosine(distances[0])

            for j in range(len(indices[0])):
                idx = int(indices[0][j])
                if idx < 0 or idx >= len(keys):
                    continue
                matched_key = keys[idx]
                if matched_key == func_key:
                    continue

                sim = float(cosine_sims[j])
                if sim < 0.70:
                    continue

                score = duplication_score(sim)
                matches.append(
                    DuplicationMatch(
                        source_function=func_key,
                        matched_function=matched_key,
                        similarity=sim,
                        duplication_score=score,
                    )
                )
        return matches

    def analyze_module(
        self, module_name: str, module_files: list[str], module_paths_cfg: list[str], k: int = 10
    ) -> DuplicationResult:
        if not _ML_AVAILABLE:
            return DuplicationResult(
                module_name=module_name,
                skipped=True,
                skip_reason='Layer 4 (duplication) skipped: install with pip install ".[ml]"',
            )

        if self._cache.is_cache_stale(module_name):
            reason = f"Cache stale: centroid for {module_name} exceeds max age"
            self._audit.log(
                EVENT_DUPLICATION_SKIPPED, module=module_name, reason=reason
            )
            return DuplicationResult(
                module_name=module_name, skipped=True, skip_reason=reason
            )

        module_file_set = set(module_files)
        index, keys, module_embeddings = self._build_faiss_index(module_file_set)

        if not keys or index is None:
            return DuplicationResult(module_name=module_name)

        matches = self._query_module_matches(
            index, keys, module_embeddings, module_name, module_paths_cfg, k
        )
        agg = float(np.mean([m.duplication_score for m in matches])) if matches else 0.0

        return DuplicationResult(
            module_name=module_name, matches=matches, aggregate_score=agg
        )
