"""FAISS similarity search and duplication scoring."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

try:
    import faiss
    import numpy as np
    import numpy.typing as npt
    _ML_AVAILABLE = True
except Exception:
    _ML_AVAILABLE = False
    faiss = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]
    npt = None  # type: ignore[assignment]

from archguard.audit.logger import AuditLogger
from archguard.cache.embeddings import EmbeddingCache
from archguard.config import EVENT_DUPLICATION_SKIPPED

logger: logging.Logger = logging.getLogger(__name__)


def duplication_score(similarity: float) -> float:
    """Compute duplication score from cosine similarity.

    Linear interpolation between 0.85 and 1.0.
    ``similarity < 0.85`` → ``0.0``.
    """
    return min(1.0, max(0.0, (similarity - 0.85) / 0.15))


@dataclass
class DuplicationMatch:
    """A single cross-module duplication match."""

    source_function: str      # "file_path::function_name"
    matched_function: str     # "file_path::function_name"
    similarity: float         # cosine similarity 0.0–1.0
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
                "ML dependencies are not installed. Run: pip install archguard[ml]"
            )

        keys = list(embeddings.keys())
        if not keys:
            index = faiss.IndexFlatL2(384)
            return index, keys

        matrix = np.array(
            [embeddings[k] for k in keys], dtype=np.float32,
        )

        # Unit-normalize
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        matrix = matrix / norms

        dim = matrix.shape[1]
        index = faiss.IndexFlatL2(dim)
        index.add(matrix)
        return index, keys

    def _l2_to_cosine(self, l2_distances: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        """Convert FAISS L2 distances to cosine similarities.

        For unit-normalized vectors: ``cosine_sim = 1 - (L2^2 / 2)``.
        Clamped to ``[0.0, 1.0]``.
        """
        if not _ML_AVAILABLE:
            raise RuntimeError(
                "ML dependencies are not installed. Run: pip install archguard[ml]"
            )
        return np.clip(1.0 - (l2_distances ** 2) / 2.0, 0.0, 1.0)

    def analyze_module(
        self,
        module_name: str,
        module_files: list[str],
        k: int = 10,
    ) -> DuplicationResult:
        """Run duplication analysis for a single module."""
        if not _ML_AVAILABLE:
            raise RuntimeError(
                "ML dependencies are not installed. Run: pip install archguard[ml]"
            )
        # 1. Check cache staleness
        if self._cache.is_cache_stale(module_name):
            reason = f"Cache stale: centroid for {module_name} exceeds max age"
            self._audit.log(EVENT_DUPLICATION_SKIPPED, module=module_name, reason=reason)
            return DuplicationResult(
                module_name=module_name,
                skipped=True,
                skip_reason=reason,
            )

        # 2. Get all embeddings from cache
        all_emb_data = self._cache.get_all_embeddings()
        if not all_emb_data:
            return DuplicationResult(module_name=module_name)

        # Extract just the arrays (drop hashes) for indexing
        all_embeddings: dict[str, npt.NDArray[np.float32]] = {
            key: v[0] for key, v in all_emb_data.items()
        }

        # 3. Build FAISS index from ALL embeddings
        index, keys = self.build_index(all_embeddings)
        if not keys:
            return DuplicationResult(module_name=module_name)

        key_to_idx: dict[str, int] = {k: i for i, k in enumerate(keys)}

        # 4. Query for each function in module_files
        matches: list[DuplicationMatch] = []
        module_file_set = set(module_files)

        # Unit-normalize query vectors
        for func_key, emb in all_embeddings.items():
            # Only query functions belonging to this module
            file_part = func_key.split("::")[0]
            if file_part not in module_file_set:
                continue

            query = emb.astype(np.float32).reshape(1, -1)
            qnorm = np.linalg.norm(query)
            if qnorm > 0:
                query = query / qnorm

            actual_k = min(k + 1, len(keys))  # +1 to exclude self
            distances, indices = index.search(query, actual_k)

            cosine_sims = self._l2_to_cosine(distances[0])

            for j in range(len(indices[0])):
                idx = int(indices[0][j])
                if idx < 0 or idx >= len(keys):
                    continue
                matched_key = keys[idx]
                if matched_key == func_key:
                    continue  # exclude self

                # Exclude same-module matches
                matched_file = matched_key.split("::")[0]
                if matched_file in module_file_set:
                    continue

                sim = float(cosine_sims[j])
                if sim < 0.70:
                    continue

                score = duplication_score(sim)
                matches.append(DuplicationMatch(
                    source_function=func_key,
                    matched_function=matched_key,
                    similarity=sim,
                    duplication_score=score,
                ))

        agg = (
            float(np.mean([m.duplication_score for m in matches]))
            if matches
            else 0.0
        )

        return DuplicationResult(
            module_name=module_name,
            matches=matches,
            aggregate_score=agg,
        )
