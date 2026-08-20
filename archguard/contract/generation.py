"""Headless contract generation.

The single public entry point for producing a ``.archguard.yml`` from a
repository. Replaces ``archguard.cli._init_dispatch._run_init_cli``, which the
dashboard had been calling directly -- constructing a fake ``typer.Context`` to
satisfy its signature -- and which dragged typer, rich, resumable checkpoints
and an interactive wizard into the web request path.

What is deliberately not carried over from the CLI wizard:

* **Checkpoints / ``--resume``.** They exist so a human can restart a long
  interactive run. A web request either completes or fails; a half-written
  checkpoint directory in a throwaway clone is litter.
* **The interactive review and profile prompt.** There is no terminal.
* **The ``GITHUB_ACTIONS`` shallow-clone guard.** The dashboard clones blobless
  but full-history precisely so this never applies.
* **``.archguard-init-summary.md``.** Written into a clone that is deleted.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from archguard.contract._discovery import (
    detect_module_communities,
    scan_commit_history,
    scan_repository,
)
from archguard.contract._synthesis import (
    build_contract,
    compute_fan_outs,
    compute_module_embeddings,
    write_contract,
)

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]


class ContractGenerationError(RuntimeError):
    """Raised when a contract cannot be generated at all."""


@dataclass(frozen=True)
class ContractGenerationResult:
    """What was generated, and on what evidence.

    ``fallback_used`` is the field callers actually need: it says whether module
    boundaries were *measured* from co-change history or *guessed* from
    directory names. Returning it explicitly is why this exists as a dataclass
    -- the previous caller had to write the YAML, read it back, and substring
    match on ``generated_by`` to find out.
    """

    path: Path
    module_count: int
    fallback_used: bool
    fallback_reason: str
    commit_count: int
    files_scanned: int


def generate_contract(
    repo_root: Path,
    output: Path | None = None,
    threshold_profile: str | None = "ci",
    min_history_commits: int = 1,
    compute_embeddings: bool = False,
    on_progress: ProgressFn | None = None,
) -> ContractGenerationResult:
    """Detect module boundaries in *repo_root* and write a contract.

    Args:
        repo_root: root of the repository to analyse. Must be a git repository
            root; a nested directory is refused rather than silently attributed
            the ancestor repository's history.
        output: where to write. Defaults to ``repo_root / ".archguard.yml"``.
        threshold_profile: a name from ``archguard.profiles.defaults.PROFILES``,
            or None to derive budgets from the repository's own measured
            fan-out. Anything generated and graded in the same pass must use a
            profile -- see ``build_contract``.
        min_history_commits: below this many commits, module detection falls
            back to the directory-name heuristic.
        compute_embeddings: also precompute per-module embedding centroids into
            the embedding cache. Requires the ML extras; a no-op without them.
        on_progress: called with human-readable status messages.

    Raises:
        ContractGenerationError: the repository has no analysable Python files.
    """
    output = output or (repo_root / ".archguard.yml")

    def emit(message: str) -> None:
        logger.info("%s", message)
        if on_progress is not None:
            on_progress(message)

    emit("Scanning repository...")
    scan = scan_repository(repo_root)
    if scan["total_files"] == 0:
        raise ContractGenerationError("No Python files found in repository.")
    emit(f"Found {scan['total_files']} Python files ({scan['total_loc']:,} LOC).")

    emit("Reading commit history...")
    history = scan_commit_history(repo_root)
    emit(
        f"Processed {history['commit_count']} commits "
        f"({history['graph_edges']} co-change pairs)."
    )

    emit("Detecting module boundaries...")
    detected = detect_module_communities(
        history["graph_data"],
        repo_root,
        scan["python_files"],
        commit_count=history["commit_count"],
        min_history=min_history_commits,
        history_status=history["history_status"],
        history_error=history["history_error"],
        on_progress=on_progress,
    )
    communities: dict[str, list[str]] = detected["communities"]
    emit(f"Detected {detected['num_communities']} modules.")

    if compute_embeddings:
        emit("Computing module embeddings...")
        embedded = compute_module_embeddings(
            communities, repo_root, scan["python_files"]
        )
        emit(
            f"Embedded {embedded['modules_embedded']} modules "
            f"using {embedded['model_name']}."
        )

    fan_outs = compute_fan_outs(communities, repo_root)

    emit("Writing contract...")
    contract = build_contract(
        communities,
        fan_outs,
        fallback_used=detected["fallback_used"],
        fallback_reason=detected["fallback_reason"],
        threshold_profile=threshold_profile,
    )
    module_count = write_contract(contract, output)

    return ContractGenerationResult(
        path=output,
        module_count=module_count,
        fallback_used=detected["fallback_used"],
        fallback_reason=detected["fallback_reason"],
        commit_count=history["commit_count"],
        files_scanned=scan["total_files"],
    )
