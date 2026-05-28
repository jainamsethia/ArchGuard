"""Coupling analysis for ArchGuard modules."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from archguard.analysis.parser import ImportEdge

logger: logging.Logger = logging.getLogger(__name__)


@dataclass
class ModuleCoupling:
    """Coupling metrics for a single architectural module."""

    module_name: str
    fan_out: int              # unique non-stdlib, non-relative imports
    fan_in: int               # number of other modules importing this one
    coupling_budget: int      # from contract or computed default
    coupling_delta: float     # see compute_coupling_delta


# ------------------------------------------------------------------
# Path helpers
# ------------------------------------------------------------------

def _normalize_path(path: str) -> str:
    """Normalize path separators and strip trailing slash."""
    return path.replace("\\", "/").rstrip("/")

from pathlib import Path

def _path_belongs_to_module(file_path: str, module_path: str) -> bool:
    """
    Check if file_path is inside module_path, with proper boundary checking.
    Prevents false matches like api_utils matching module 'api'.
    """
    # Normalize both to forward-slash, strip leading slash
    file_parts = Path(file_path.strip("/")).parts
    module_parts = Path(module_path.strip("/")).parts
    if len(file_parts) < len(module_parts):
        return False
    # Compare part-by-part, not as raw strings
    return file_parts[:len(module_parts)] == module_parts


def _file_belongs_to_module(file_path: str, paths: list[str]) -> bool:
    """Check if *file_path* starts with any of the module's path prefixes."""
    return any(_path_belongs_to_module(file_path, p) for p in paths)


def _assign_file_to_module(
    file_path: str,
    module_paths: dict[str, list[str]],
    *,
    _warned: set[str] | None = None,
) -> str | None:
    """Assign a file to a module using longest prefix match.

    FR-03 priority:
    1. test paths (contains ``/test/`` or ``/tests/``) → skip
    2. longest prefix match on *module_paths*
    3. unassigned → log warning once, skip
    """
    normalized = _normalize_path(file_path)

    # Skip test paths
    if "/test/" in normalized or "/tests/" in normalized:
        return None

    best_match: str | None = None
    best_len: int = 0
    for mod_name, paths in module_paths.items():
        for p in paths:
            if _path_belongs_to_module(file_path, p):
                prefix_len = len(_normalize_path(p))
                if prefix_len > best_len:
                    best_match = mod_name
                    best_len = prefix_len

    if best_match is None:
        warned = _warned if _warned is not None else set()
        if file_path not in warned:
            logger.warning("File %s not assigned to any module", file_path)
            warned.add(file_path)

    return best_match


# ------------------------------------------------------------------
# Fan-out / fan-in
# ------------------------------------------------------------------

def compute_fan_out(
    edges: list[ImportEdge],
    module_name: str,
    module_paths: dict[str, list[str]],
) -> int:
    """Count unique non-stdlib, non-relative imported modules for files
    belonging to *module_name*."""
    paths = module_paths.get(module_name, [])
    unique_roots: set[str] = set()

    for edge in edges:
        if edge.is_stdlib or edge.is_relative:
            continue
        if not _file_belongs_to_module(edge.source_file, paths):
            continue
        root = edge.imported_module.split(".")[0]
        unique_roots.add(root)

    return len(unique_roots)


def compute_fan_in(
    edges: list[ImportEdge],
    module_name: str,
    module_paths: dict[str, list[str]],
) -> int:
    """Count unique other modules that import *module_name*."""
    target_paths = module_paths.get(module_name, [])
    if not target_paths:
        return 0

    importing_modules: set[str] = set()

    for edge in edges:
        if edge.is_stdlib or edge.is_relative:
            continue

        # Does this import target our module?
        import_as_path = edge.imported_module.replace(".", "/")
        targets_us = any(
            _path_belongs_to_module(import_as_path, tp)
            or _path_belongs_to_module(tp, import_as_path)
            for tp in target_paths
        )
        if not targets_us:
            continue

        # Which module does the source file belong to?
        source_module = _assign_file_to_module(edge.source_file, module_paths)
        if source_module is not None and source_module != module_name:
            importing_modules.add(source_module)

    return len(importing_modules)


# ------------------------------------------------------------------
# Coupling formulas
# ------------------------------------------------------------------

def compute_coupling_delta(
    fan_out: int,
    coupling_budget: int,
    module_name: str = "",
) -> float:
    """Compute CouplingDelta from fan_out and coupling_budget.

    Formula:
      - fan_out=0, coupling_budget=0 → 1.0 (with warning)
      - fan_out ≤ coupling_budget    → 0.0
      - otherwise                    → min(1.0, (fan_out − budget) / max(budget, 1))
    """
    if fan_out == 0 and coupling_budget == 0:
        logger.warning(
            "Module %s has fan_out=0 and coupling_budget=0, "
            "CouplingDelta set to 1.0",
            module_name or "<unknown>",
        )
        return 1.0
    if fan_out <= coupling_budget:
        return 0.0
    return min(1.0, (fan_out - coupling_budget) / max(coupling_budget, 1))


def default_coupling_budget(fan_out_at_init: int) -> int:
    """Compute default coupling budget from playbook formula.

    Formula: ``max(3, ceil(fan_out_at_init * 1.5))``
    """
    return max(3, math.ceil(fan_out_at_init * 1.5))


# ------------------------------------------------------------------
# Orchestrator
# ------------------------------------------------------------------

def analyze_coupling(
    edges: list[ImportEdge],
    module_paths: dict[str, list[str]],
    budgets: dict[str, int],
) -> list[ModuleCoupling]:
    """Compute :class:`ModuleCoupling` for every module in *module_paths*."""
    results: list[ModuleCoupling] = []

    for module_name in module_paths:
        fan_out_val = compute_fan_out(edges, module_name, module_paths)
        fan_in_val = compute_fan_in(edges, module_name, module_paths)

        budget = budgets.get(module_name)
        if budget is None:
            budget = default_coupling_budget(fan_out_val)

        delta = compute_coupling_delta(fan_out_val, budget, module_name)

        results.append(ModuleCoupling(
            module_name=module_name,
            fan_out=fan_out_val,
            fan_in=fan_in_val,
            coupling_budget=budget,
            coupling_delta=delta,
        ))

    return results
