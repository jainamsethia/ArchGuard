"""Utility helpers for AnalysisOrchestrator."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Callable
import typing

if typing.TYPE_CHECKING:
    from archguard.analysis.layers import AnalysisResult

from archguard.analysis.scoring import (
    LayerScores,
    compute_archdebt,
)
from archguard.utils.paths import path_belongs_to_module

logger: logging.Logger = logging.getLogger(__name__)


def _build_partial_result(
    repo_root: Path,
    contract: dict[str, Any],
    filter_fn: Callable[[Path, list[Any]], list[Any]],
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
) -> "AnalysisResult":
    """Build a partial AnalysisResult for fail-fast scenarios."""
    from archguard.analysis.layers import AnalysisResult

    violations = filter_fn(repo_root, violations)
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
        rel = fpath.relative_to(repo_root)
        parts = list(rel.with_suffix("").parts)
        if parts and parts[0] == "src":
            parts = parts[1:]
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    for fpath in changed_files:
        rel = (
            str(fpath.relative_to(repo_root)).replace("\\", "/")
            if fpath.is_absolute()
            else str(fpath).replace("\\", "/")
        )
        dotted_module = _resolve_module_name(
            fpath if fpath.is_absolute() else repo_root / fpath
        )

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
