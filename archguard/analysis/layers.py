"""Layer 1–4 analysis orchestration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Self

from archguard.analysis._models import AnalysisResult as AnalysisResult
from archguard.analysis._models import ViolationDetail as ViolationDetail
from archguard.analysis._orchestrator_utils import (
    get_commit_sha as _get_commit_sha_fn,
)
from archguard.cache.db import EmbeddingDB
from archguard.cache.embeddings import EmbeddingCache
from archguard.contract.loader import load_contract

logger: logging.Logger = logging.getLogger(__name__)


def _get_module_paths(mod: dict[str, Any]) -> list[str]:
    """Normalize 'path' (str or list) and 'paths' (list) into a unified list."""
    from archguard.analysis._orchestrator_utils import (
        _get_module_paths as _get_module_paths_fn,
    )

    return _get_module_paths_fn(mod)


class AnalysisOrchestrator:
    """Orchestrates the full Layer 1–4 analysis pipeline."""

    def __init__(
        self,
        repo_root: Path,
        db_path: Path | None = None,
        suppressed_hashes: set[str] | None = None,
    ) -> None:
        """
        Args:
            repo_root:        root of the tree being analysed.
            db_path:          embedding cache location.
            suppressed_hashes: violation identities to hide, from
                ``suppression.models.make_violation_hash``. Resolved by the
                caller because whose suppressions apply is a question about the
                account that submitted the job, not about the tree on disk --
                and because a throwaway clone never holds them anyway.
        """
        self.repo_root = repo_root
        self.contract: dict[str, Any] = load_contract(repo_root)
        db_path = db_path or repo_root / ".archguard-cache" / "embeddings.db"
        self.db = EmbeddingDB(db_path)
        self.cache = EmbeddingCache(self.db)
        self.suppressed_hashes = suppressed_hashes
        self._audit: Any | None = None

    def close(self) -> None:
        """Close database connections and release resources."""
        if hasattr(self, "db") and self.db is not None:
            self.db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def run(
        self,
        changed_files: list[Path],
        commit_sha: str,
        progress_callback: Any = None,
        fail_fast: bool = False,
        quiet: bool = False,
        repo_files: list[Path] | None = None,
    ) -> AnalysisResult:
        """Run the full Layer 1–4 pipeline.

        *repo_files* is every file in the repository, which on an incremental
        scan is wider than *changed_files*. Layers 1, 2 and 4 measure against
        it so that their scores describe the repository rather than whichever
        slice happened to change; Layer 3, the only layer whose per-module cost
        is a model rather than a parse, still measures *changed_files*.
        ``None`` means "the same as changed_files", which is what a full
        analysis passes.
        """
        from archguard.analysis._orchestrator_run import _run_orchestrator
        from archguard.analysis.coupling import collect_unassigned

        # One run, one summary of the files the contract does not cover.
        # `_assign_file_to_module` is called once per edge by the payload
        # builder and once per (module x edge) by `compute_fan_in`, so a single
        # uncovered file used to be logged dozens of times per analysis. This
        # is the analysis boundary, which is what "once per run" has to mean.
        with collect_unassigned(context="analysis"):
            return _run_orchestrator(
                self,
                changed_files,
                commit_sha,
                progress_callback,
                fail_fast,
                quiet,
                repo_files,
            )

    # (Removed wrappers to save lines)

    @staticmethod
    def get_commit_sha(repo_root: Path) -> str:
        """Read HEAD commit SHA, return 7-char short form. Does not throw on failure."""
        return _get_commit_sha_fn(repo_root)
