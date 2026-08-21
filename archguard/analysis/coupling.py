"""Coupling analysis for ArchGuard modules."""

from __future__ import annotations

import logging
import math
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from archguard.analysis.parser import ImportEdge
from archguard.utils.paths import normalize_path, path_belongs_to_module

logger: logging.Logger = logging.getLogger(__name__)

#: Paths already reported as unassigned in the current analysis, or None when
#: no analysis scope is open.
#:
#: A ContextVar rather than a module global because a worker runs more than one
#: analysis at a time in one process (``ARCHGUARD_WORKER_CONCURRENCY``): a
#: shared set would let one job's file suppress another job's warning and mix
#: two repositories into one summary. Each analysis arrives through
#: ``asyncio.to_thread``, which copies the context, so each gets its own.
_unassigned: ContextVar[set[str] | None] = ContextVar("unassigned_files", default=None)

#: How many paths the summary names before it stops. A repository whose
#: contract covers nothing produces a list as long as the file count, and a
#: log line that long is skipped rather than read.
MAX_SUMMARISED_PATHS = 20


@contextmanager
def collect_unassigned(context: str = "") -> Iterator[set[str]]:
    """Report each unassigned file once, as one summary at the end.

    ``_assign_file_to_module`` is called once per edge by the payload builder,
    and once per (module x edge) by ``compute_fan_in`` -- so a single file that
    matches no module was logged dozens of times per analysis. Measured on a
    four-file repository: sixty-plus identical lines in one run. On a repository
    with hundreds of unmatched files it is thousands, in a worker whose logs are
    the only way to debug it.

    The fact itself is worth keeping and is not lowered to debug: a file
    matching no module means the contract's paths do not cover it, so it is
    silently excluded from scoring. It is the repetition that carries no
    information.

    Re-entrant: a nested scope joins the outer one rather than starting a second
    tally, so the summary is per analysis rather than per stage.
    """
    existing = _unassigned.get()
    if existing is not None:
        # Already inside a scope. Do not reset it, and do not summarise on the
        # way out -- the outer scope owns both.
        yield existing
        return

    seen: set[str] = set()
    token = _unassigned.set(seen)
    try:
        yield seen
    finally:
        _unassigned.reset(token)
        if seen:
            shown = sorted(seen)[:MAX_SUMMARISED_PATHS]
            more = len(seen) - len(shown)
            where = f" in {context}" if context else ""
            logger.warning(
                "%d file(s)%s matched no module in the contract and were "
                "excluded from scoring: %s%s",
                len(seen),
                where,
                ", ".join(shown),
                f" (and {more} more)" if more else "",
            )


@dataclass
class ModuleCoupling:
    """Coupling metrics for a single architectural module."""

    module_name: str
    fan_out: int  # unique non-stdlib, non-relative imports
    fan_in: int  # number of other modules importing this one
    coupling_budget: int  # from contract or computed default
    coupling_delta: float  # see compute_coupling_delta


# ------------------------------------------------------------------
# Path helpers
# ------------------------------------------------------------------


def _assign_file_to_module(
    file_path: str,
    module_paths: dict[str, list[str]],
) -> str | None:
    """Assign a file to a module using longest prefix match.

    FR-03 priority:
    1. test paths (contains ``/test/`` or ``/tests/``) -> skip
    2. longest prefix match on *module_paths*
    3. unassigned -> recorded for the run summary, skip

    Unassigned files are recorded rather than logged here. Inside a
    ``collect_unassigned`` scope they are reported once, together, when the
    scope closes; outside one -- a direct call, or a test -- the warning is
    emitted immediately, so a library caller is not silently deprived of it.

    The old ``_warned`` parameter is gone. It existed to deduplicate, but its
    default built a fresh set per call, so it deduplicated nothing, and no
    caller ever passed one.
    """
    normalized = normalize_path(file_path)

    # Skip test paths
    if "/test/" in normalized or "/tests/" in normalized:
        return None

    best_match: str | None = None
    best_len: int = 0
    for mod_name, paths in module_paths.items():
        for p in paths:
            if path_belongs_to_module(file_path, [p]):
                prefix_len = len(normalize_path(p))
                if prefix_len > best_len:
                    best_match = mod_name
                    best_len = prefix_len

    if best_match is None:
        seen = _unassigned.get()
        if seen is None:
            logger.warning("File %s not assigned to any module", file_path)
        else:
            seen.add(file_path)

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
        # Relative imports are intra-package by definition and thus don't represent
        # cross-module architectural coupling. We exclude them from fan-out.
        if edge.is_stdlib or edge.is_relative:
            continue
        if not path_belongs_to_module(edge.source_file, paths):
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
        # Relative imports are intra-package by definition and thus don't represent
        # cross-module architectural coupling. We exclude them from fan-in.
        if edge.is_stdlib or edge.is_relative:
            continue

        # Does this import target our module?
        import_as_path = edge.imported_module.replace(".", "/")
        targets_us = any(
            path_belongs_to_module(import_as_path, [tp])
            or path_belongs_to_module(tp, [import_as_path])
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
      - fan_out=0, coupling_budget=0 -> 1.0 (with warning)
      - fan_out ≤ coupling_budget    -> 0.0
      - otherwise                    -> min(1.0, (fan_out − budget) / max(budget, 1))
    """
    if fan_out == 0 and coupling_budget == 0:
        logger.warning(
            "Module %s has fan_out=0 and coupling_budget=0, CouplingDelta set to 1.0",
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

_PROJECT_DEFAULT_FAN_OUT_BASELINE = 5

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
            budget = default_coupling_budget(_PROJECT_DEFAULT_FAN_OUT_BASELINE)

        delta = compute_coupling_delta(fan_out_val, budget, module_name)

        results.append(
            ModuleCoupling(
                module_name=module_name,
                fan_out=fan_out_val,
                fan_in=fan_in_val,
                coupling_budget=budget,
                coupling_delta=delta,
            )
        )

    return results
