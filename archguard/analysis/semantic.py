"""MiniLM embedding pipeline and semantic drift detection."""

from __future__ import annotations

import ast
import hashlib
import logging
import threading
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

try:
    import numpy as np
    import numpy.typing as npt

    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False
    np: typing.Any = None  # type: ignore[no-redef]
    npt: typing.Any = None  # type: ignore[no-redef]

try:
    from sentence_transformers import SentenceTransformer

    _ML_AVAILABLE = _NUMPY_AVAILABLE
except ImportError:
    _ML_AVAILABLE = False
    SentenceTransformer: typing.Any = None  # type: ignore[no-redef]

from archguard.cache.embeddings import EmbeddingCache

logger = logging.getLogger(__name__)


@dataclass
class FunctionChunk:
    """A single function extracted from source code."""

    file_path: str
    function_name: str
    source: str  # docstring + body combined
    content_hash: str  # SHA256 of source


@dataclass
class SemanticDriftResult:
    """Result of semantic drift analysis for one module."""

    module_name: str
    drift_score: float  # cosine distance 0.0–1.0
    pre_pr_centroid: npt.NDArray[np.float32]
    post_pr_centroid: npt.NDArray[np.float32]
    functions_analyzed: int
    cache_hit: bool
    skipped: bool = False
    skip_reason: str = ""


# ------------------------------------------------------------------
# Cosine helpers
# ------------------------------------------------------------------


def cosine_distance(a: npt.NDArray[np.float32], b: npt.NDArray[np.float32]) -> float:
    """``1 - cosine_similarity``, clamped to ``[0.0, 1.0]``."""
    if not _ML_AVAILABLE:
        raise RuntimeError(
            "Layer 3 (Semantic Drift) requires ML dependencies. "
            "Install them with: pip install -e \".[ml]\"\n"
            "To skip this layer, set `skip_layers: [semantic]` in .archguard.yml"
        )
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0
    sim = float(np.dot(a, b) / (norm_a * norm_b))
    return float(np.clip(1.0 - sim, 0.0, 1.0))


# ------------------------------------------------------------------

_GLOBAL_MODEL_CACHE: dict[str, Any] = {}
_MODEL_LOCK = threading.Lock()


def _get_model(model_name: str) -> SentenceTransformer:
    if model_name not in _GLOBAL_MODEL_CACHE:
        with _MODEL_LOCK:
            if model_name not in _GLOBAL_MODEL_CACHE:
                if not _ML_AVAILABLE:
                    raise RuntimeError(
                        "Layer 3 (Semantic Drift) requires ML dependencies. "
                        "Install them with: pip install -e \".[ml]\""
                    )
                from sentence_transformers import SentenceTransformer

                _GLOBAL_MODEL_CACHE[model_name] = SentenceTransformer(model_name)
    return cast("SentenceTransformer", _GLOBAL_MODEL_CACHE[model_name])


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

    MAX_FUNCTIONS = 100
    MAX_CHARS = 50000
    functions_processed = 0
    total_chars = len(module_doc) if module_doc else 0

    # Class and function docstrings + names
    for node in ast.walk(tree):
        if functions_processed >= MAX_FUNCTIONS or total_chars >= MAX_CHARS:
            break

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            texts.append(node.name)  # function/class name as semantic signal
            total_chars += len(node.name)
            functions_processed += 1

            doc = ast.get_docstring(node)
            if doc:
                texts.append(doc)
                total_chars += len(doc)

    return " ".join(texts)[:MAX_CHARS]


class SemanticAnalyzer:
    """Embedding pipeline + drift detection using all-MiniLM-L6-v2."""

    MODEL_NAME = "all-MiniLM-L6-v2"

    def __init__(self, cache: EmbeddingCache) -> None:
        self._cache: EmbeddingCache = cache
        self._model: SentenceTransformer | None = None

    @property
    def _sentence_transformer(self) -> SentenceTransformer:
        if self._model is None:
            self._model = _get_model(self.MODEL_NAME)
        return self._model

    def embed_chunks(
        self,
        chunks: list[FunctionChunk],
        batch_size: int = 32,
    ) -> dict[str, npt.NDArray[np.float32]]:
        """Embed function chunks, using cache where possible.

        Lazy-imports ``SentenceTransformer("all-MiniLM-L6-v2")``.
        Returns ``{"{file_path}::{function_name}": embedding}``.
        """
        if not _ML_AVAILABLE:
            raise RuntimeError(
                "Layer 3 (Semantic Drift) requires ML dependencies. "
                "Install them with: pip install -e \".[ml]\"\n"
                "To skip this layer, set `skip_layers: [semantic]` in .archguard.yml"
            )
        result: dict[str, npt.NDArray[np.float32]] = {}
        to_embed: list[FunctionChunk] = []

        # Check cache first using batch get
        all_keys = [
            f"{c.file_path}::{c.function_name}::{c.content_hash}" for c in chunks
        ]
        cached_batch = self._cache.get_batch(all_keys)

        for chunk, key in zip(chunks, all_keys, strict=True):
            cached = cached_batch.get(key)
            result_key = f"{chunk.file_path}::{chunk.function_name}"
            if cached is not None:
                result[result_key] = cached
            else:
                to_embed.append(chunk)

        # Embed cache misses
        if to_embed:
            model = self._sentence_transformer
            texts = [c.source for c in to_embed]
            embeddings = model.encode(texts, batch_size=batch_size)
            model_name = self.MODEL_NAME

            new_items = {}
            for i, chunk in enumerate(to_embed):
                result_key = f"{chunk.file_path}::{chunk.function_name}"
                emb = np.array(embeddings[i], dtype=np.float32)
                result[result_key] = emb

                insert_key = f"{chunk.file_path}::{chunk.function_name}::{chunk.content_hash}::{model_name}"
                new_items[insert_key] = emb

            self._cache.set_batch(new_items)

        return result

    def embed_files(
        self,
        files: list[Path],
        repo_root: Path,
        context: str = "",
    ) -> dict[str, npt.NDArray[np.float32]]:
        """Embed every function in *files*, writing them to the shared cache.

        Extracted from ``compute_drift`` so Layer 4 can populate the same cache
        without computing drift. Layer 4 searches the embeddings table, and
        until this existed only Layer 3 ever wrote to it -- over the modules a
        scan re-analysed. On an incremental scan every unchanged file was
        therefore missing a vector, so a clone of one was invisible no matter
        how wide a file list Layer 4 was handed.

        Returns ``{"{rel_path}::{function_name}": embedding}``. Embedding is
        cache-aware (``embed_chunks`` skips anything already stored), so
        re-passing files another layer has embedded costs a batched lookup
        rather than a re-encode.

        Returns empty without ML rather than raising: ``embed_chunks`` raises,
        and the Layer 4 caller would turn that into "Layer 4 analysis failed"
        on a machine that simply has no ML extras installed.

        *context* is the subject of the warning below, supplied whole by the
        caller rather than assembled here -- the two callers are measuring
        different things, and a message that named drift would be wrong coming
        from the duplication corpus.
        """
        if not _ML_AVAILABLE:
            return {}

        MAX_FILES = 500
        processed_files = 0
        all_embeddings: dict[str, npt.NDArray[np.float32]] = {}
        unreadable_files: list[str] = []
        failed_files: list[str] = []
        for fpath in files:
            if processed_files >= MAX_FILES:
                break
            processed_files += 1
            try:
                rel = str(fpath.relative_to(repo_root)).replace("\\", "/")

                try:
                    file_content = fpath.read_text(encoding="utf-8")
                    tree = ast.parse(file_content)
                except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
                    # A file we cannot parse contributes no functions, which
                    # silently pulls the centroid toward "no drift". Record it.
                    unreadable_files.append(f"{fpath.name}: {type(exc).__name__}")
                    continue

                chunks = []
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        source = ast.get_source_segment(file_content, node)
                        if source:
                            content_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
                            chunks.append(FunctionChunk(
                                file_path=rel,
                                function_name=node.name,
                                source=source,
                                content_hash=content_hash,
                            ))

                if chunks:
                    embedded = self.embed_chunks(chunks)
                    all_embeddings.update(embedded)
            except Exception as exc:
                failed_files.append(f"{getattr(fpath, 'name', fpath)}: {type(exc).__name__}: {exc}")
                logger.debug("Embedding failed for %s", fpath, exc_info=True)
                continue

        if unreadable_files or failed_files:
            # Without this, a module whose files all failed to embed is
            # indistinguishable from a module that genuinely did not drift.
            logger.warning(
                "%s: %d/%d file(s) contributed no embeddings (%d unparseable, "
                "%d errored). The result is computed from the remainder and "
                "understates what is really there. First failures: %s",
                context,
                len(unreadable_files) + len(failed_files),
                processed_files,
                len(unreadable_files),
                len(failed_files),
                (unreadable_files + failed_files)[:5],
            )

        return all_embeddings

    def compute_centroid(
        self,
        embeddings: dict[str, npt.NDArray[np.float32]],
    ) -> npt.NDArray[np.float32]:
        """Mean of all embeddings, normalized to unit length.

        Raises ``ValueError`` if *embeddings* is empty.
        """
        if not _ML_AVAILABLE:
            raise RuntimeError(
                "Layer 3 (Semantic Drift) requires ML dependencies. "
                "Install them with: pip install -e \".[ml]\"\n"
                "To skip this layer, set `skip_layers: [semantic]` in .archguard.yml"
            )
        if not embeddings:
            from archguard.utils.errors import AnalysisError

            raise AnalysisError("Cannot compute centroid of empty embeddings")

        matrix = np.array(list(embeddings.values()), dtype=np.float32)
        centroid: npt.NDArray[np.float32] = np.mean(matrix, axis=0)
        norm = float(np.linalg.norm(centroid))
        if norm > 0.0:
            centroid = centroid / norm
        return centroid

    def _store_baseline(
        self, module_name: str, centroid: npt.NDArray[np.float32]
    ) -> None:
        """Persist *centroid* so the next run has something to compare against."""
        centroid_hash = hashlib.sha256(
            centroid.astype(np.float32).tobytes(),
        ).hexdigest()
        self._cache.store_centroid(module_name, centroid, centroid_hash)

    def compute_drift(
        self,
        module_name: str,
        changed_files: list[Path],
        repo_root: Path,
    ) -> SemanticDriftResult:
        """Compute semantic drift for a module based on changed files."""
        if not _ML_AVAILABLE:
            return SemanticDriftResult(
                module_name=module_name,
                drift_score=0.0,
                pre_pr_centroid=np.zeros(384, dtype=np.float32) if _NUMPY_AVAILABLE else None, # type: ignore
                post_pr_centroid=np.zeros(384, dtype=np.float32) if _NUMPY_AVAILABLE else None, # type: ignore
                functions_analyzed=0,
                cache_hit=False,
                skipped=True,
                skip_reason='Layer 3 (Semantic Drift) skipped: install with pip install -e ".[ml]"',
            )
        # 1. Load pre-PR centroid
        cached = self._cache.get_centroid(module_name)
        cache_hit = cached is not None
        pre_centroid: npt.NDArray[np.float32]
        if cached is not None:
            pre_centroid = cached[0]
        else:
            pre_centroid = np.zeros(384, dtype=np.float32)

        # 2. Extract + embed functions from changed files
        all_embeddings = self.embed_files(
            changed_files, repo_root, context=f"Semantic drift for module {module_name}"
        )

        # 3. Compute post-PR centroid
        if not all_embeddings:
            return SemanticDriftResult(
                module_name=module_name,
                drift_score=0.0,
                pre_pr_centroid=pre_centroid,
                post_pr_centroid=pre_centroid,
                functions_analyzed=0,
                cache_hit=cache_hit,
                skipped=True,
                skip_reason=(
                    "no functions found to embed - semantic drift not measured"
                ),
            )

        post_centroid = self.compute_centroid(all_embeddings)

        # 4. Compute drift
        if not cache_hit:
            # Drift is a comparison against a stored baseline centroid. On a
            # first analysis there is nothing to compare to, so 0.0 here means
            # "not measured", not "measured, no drift". Reporting it as a clean
            # pass is how a check that never ran ends up contributing a
            # perfect score -- every one-off dashboard run clones fresh, so
            # this is the normal case there, not an edge case.
            self._store_baseline(module_name, post_centroid)
            return SemanticDriftResult(
                module_name=module_name,
                drift_score=0.0,
                pre_pr_centroid=pre_centroid,
                post_pr_centroid=post_centroid,
                functions_analyzed=len(all_embeddings),
                cache_hit=False,
                skipped=True,
                skip_reason=(
                    "no prior baseline - semantic drift is not available on a "
                    "first scan of a repository"
                ),
            )

        drift = cosine_distance(pre_centroid, post_centroid)

        # 5. Update stored centroid
        self._store_baseline(module_name, post_centroid)

        return SemanticDriftResult(
            module_name=module_name,
            drift_score=drift,
            pre_pr_centroid=pre_centroid,
            post_pr_centroid=post_centroid,
            functions_analyzed=len(all_embeddings),
            cache_hit=cache_hit,
        )
