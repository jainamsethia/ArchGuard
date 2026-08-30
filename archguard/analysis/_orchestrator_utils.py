"""Utility helpers for AnalysisOrchestrator."""

from __future__ import annotations

import logging
import subprocess
import typing
from pathlib import Path
from typing import Any

if typing.TYPE_CHECKING:
    from archguard.analysis.layers import AnalysisResult

    class SuppressionFilter(typing.Protocol):
        """Signature of _suppression_filter._filter_suppressed."""

        def __call__(
            self,
            repo_root: Path,
            violations: list[Any],
            suppressed_hashes: set[str] | None = None,
        ) -> list[Any]: ...

from archguard.analysis.scoring import (
    LayerScores,
    compute_archdebt,
)
from archguard.utils.paths import path_belongs_to_module

logger: logging.Logger = logging.getLogger(__name__)


def _build_partial_result(
    repo_root: Path,
    contract: dict[str, Any],
    filter_fn: SuppressionFilter,
    l1: float,
    l2: float,
    l3: float,
    l4: float,
    skipped_layers: list[str],
    violations: list[Any],
    affected: dict[str, list[Path]],
    rel_files: list[str],
    commit_sha: str,
    metrics: dict[str, Any],
    suppressed_hashes: set[str] | None = None,
) -> AnalysisResult:
    """Build a partial AnalysisResult for fail-fast scenarios."""
    from archguard.analysis.layers import AnalysisResult

    # Same store the full path uses: a fail-fast run must not resurrect
    # violations the user already suppressed.
    violations = filter_fn(repo_root, violations, suppressed_hashes=suppressed_hashes)
    scores = LayerScores(l1, l2, l3, l4)

    weights_cfg = contract.get("weights")
    if weights_cfg and isinstance(weights_cfg, dict):
        weights = (
            float(weights_cfg.get("layer1", 0.25)),
            float(weights_cfg.get("layer2", 0.25)),
            float(weights_cfg.get("layer3", 0.25)),
            float(weights_cfg.get("layer4", 0.25)),
        )
    else:
        weights = (0.25, 0.25, 0.25, 0.25)

    archdebt = compute_archdebt(
        scores,
        weights=weights,
        fail_threshold=float(contract.get("fail_threshold", 0.75)),
        warn_threshold=float(contract.get("warn_threshold", 0.50)),
    )
    # Even if partial, we set should_fail_ci to True if it triggered fail-fast
    archdebt.should_fail_ci = True

    res = AnalysisResult(
        archdebt=archdebt,
        violations=violations,
        layer_scores=scores,
        modules_analyzed=len(affected),
        changed_files=rel_files,
        commit_sha=commit_sha,
        metrics=metrics,
    )
    res.fail_fast_triggered = True
    res.skipped_layers_names = skipped_layers
    return res


def _get_affected_modules(
    repo_root: Path,
    contract: dict[str, Any],
    changed_files: list[Path],
) -> dict[str, list[Path]]:
    """Map changed files to their module names using contract paths or module_names."""
    from archguard.analysis.layers import _get_module_paths

    modules_cfg = contract.get("modules", [])
    result: dict[str, list[Path]] = {}

    def _resolve_module_name(fpath: Path) -> str:
        rel = _repo_relative(fpath)
        parts = list(rel.with_suffix("").parts)
        if parts and parts[0] == "src":
            parts = parts[1:]
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    def _repo_relative(fpath: Path) -> Path:
        """Return *fpath* as a path relative to repo_root.

        Handles absolute paths, CWD-relative paths, and paths that are already
        relative to repo_root. This is essential: the module-prefix match in the
        loop below keys on a repo-root-relative string, and a CWD-relative path
        (e.g. ``tests/.../module_a/a.py``) would otherwise never match a module
        path like ``module_a/`` — silently leaving ``affected`` empty and
        starving Layers 3 & 4 of input.

        Among plausible interpretations we pick the one yielding the SHORTEST
        repo-relative path: ``Path.relative_to`` does pure prefix matching, so a
        doubled path can spuriously "succeed" while still being wrong.
        """
        root_resolved = repo_root.resolve()
        if fpath.is_absolute():
            return fpath.relative_to(root_resolved)
        # Relative: could be relative-to-CWD or relative-to-repo_root. Try both
        # and keep the shortest non-empty repo-relative result.
        candidates = [fpath, repo_root / fpath]
        best: Path | None = None
        for candidate in candidates:
            try:
                rel = candidate.resolve().relative_to(root_resolved)
            except ValueError:
                continue
            if best is None or len(rel.parts) < len(best.parts):
                best = rel
        if best is not None:
            return best
        return Path(str(fpath))

    for fpath in changed_files:
        rel = str(_repo_relative(fpath)).replace("\\", "/")
        dotted_module = _resolve_module_name(fpath)

        for mod in modules_cfg:
            mod_name: str = mod["name"]
            paths: list[str] = _get_module_paths(mod)
            module_names: list[str] = mod.get("module_names", [])

            matched = False
            for p in paths:
                if path_belongs_to_module(rel, [p]):
                    matched = True
                    break

            if not matched:
                for m_name in module_names:
                    if dotted_module == m_name or dotted_module.startswith(
                        m_name + "."
                    ):
                        matched = True
                        break

            if matched:
                result.setdefault(mod_name, []).append(fpath)
                break

    return result


def get_commit_sha(repo_root: Path) -> str:
    """Read HEAD commit SHA, return 7-char short form. Does not throw on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()[:7]
    except Exception as e:
        logger.debug(f"Failed to get commit SHA: {e}")
    return "unknown"


def _get_module_paths(mod: dict[str, Any]) -> list[str]:
    """Normalize path or paths into a unified list."""
    if "path" in mod:
        v = mod["path"]
        return [v] if isinstance(v, str) else list(v)
    return list(mod.get("paths", []))
