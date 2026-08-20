"""PR Risk Analysis (Phase 4 Step 15)."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from archguard.utils.paths import normalize_path, path_belongs_to_module

logger = logging.getLogger(__name__)


@dataclass
class AtRiskModule:
    """Represents a module at risk due to PR changes."""

    module: str
    risk_level: str
    reasons: list[str] = field(default_factory=list)


@dataclass
class PRRiskReport:
    """Complete PR risk analysis report."""

    overall_risk: str
    risk_score: int
    module_risks: list[AtRiskModule] = field(default_factory=list)

    @property
    def at_risk_modules(self) -> list[AtRiskModule]:
        return self.module_risks


class PRRiskAnalyzer:
    """Analyzes PR changes to compute risk score and classification."""

    def __init__(self) -> None:
        pass

    def _assign_file_to_module(
        self, file_path: str, module_paths: dict[str, list[str]]
    ) -> str | None:
        """Assign a file to a module using longest prefix match."""
        normalized = normalize_path(file_path)

        if "/test/" in normalized or "/tests/" in normalized:
            return None

        best_match: str | None = None
        best_len: int = 0
        for mod_name, paths in module_paths.items():
            for p in paths:
                if normalize_path(file_path) == normalize_path(
                    p
                ) or path_belongs_to_module(file_path, [p]):
                    prefix_len = len(normalize_path(p))
                    if prefix_len > best_len:
                        best_match = mod_name
                        best_len = prefix_len

        return best_match

    def _get_downstream_modules(
        self,
        changed_modules: set[str],
        dependency_graph: dict[str, list[str]],
    ) -> set[str]:
        """Find modules that transitively depend on the changed modules.

        dependency_graph represents module -> [modules it depends on].
        So if A depends on B, the graph has graph[A] = [B].
        If B changes, A is downstream of B.
        """
        # Invert graph: from -> [modules that depend on from]
        inverted_graph: dict[str, list[str]] = {}
        for mod, dependencies in dependency_graph.items():
            for dep in dependencies:
                inverted_graph.setdefault(dep, []).append(mod)

        downstream: set[str] = set()
        queue = deque(changed_modules)

        while queue:
            current = queue.popleft()
            for dependent in inverted_graph.get(current, []):
                if dependent not in changed_modules and dependent not in downstream:
                    downstream.add(dependent)
                    queue.append(dependent)

        return downstream

    def _compute_classification(self, score: int) -> str:
        """Classify risk score."""
        if score == 0:
            return "none"
        if score <= 20:
            return "low"
        if score <= 50:
            return "medium"
        if score <= 100:
            return "high"
        return "critical"

    def analyze(
        self,
        changed_files: list[str],
        module_paths: dict[str, list[str]],
        dependency_graph: Any | None = None,
    ) -> PRRiskReport:
        """Analyze PR changes to compute risk."""

        # 1. Identify directly affected modules
        direct_modules: set[str] = set()
        for f in changed_files:
            mod = self._assign_file_to_module(f, module_paths)
            if mod:
                direct_modules.add(mod)

        # 2. Identify transitive downstream modules
        transitive_modules: set[str] = set()
        if dependency_graph is not None:
            if hasattr(dependency_graph, "successors"):
                dg = {}
                for node in dependency_graph.nodes:
                    dg[node] = list(dependency_graph.successors(node))
                dependency_graph = dg
            elif hasattr(dependency_graph, "adj"):
                dg = {}
                for node in dependency_graph.nodes:
                    dg[node] = list(dependency_graph.adj[node])
                dependency_graph = dg
            transitive_modules = self._get_downstream_modules(
                direct_modules, dependency_graph
            )

        # 3. Compute Risk Score
        # E.g., 10 points per direct module, 2 points per transitive module
        risk_score = len(direct_modules) * 10 + len(transitive_modules) * 2

        # 4. Classify risk
        overall_risk = self._compute_classification(risk_score)

        # 5. Build Module Risks
        module_risks: list[AtRiskModule] = []
        for mod in sorted(direct_modules):
            module_risks.append(
                AtRiskModule(
                    module=mod,
                    risk_level="high",
                    reasons=["Directly modified"],
                )
            )

        for mod in sorted(transitive_modules):
            module_risks.append(
                AtRiskModule(
                    module=mod,
                    risk_level="low",
                    reasons=["Transitively affected via dependencies"],
                )
            )

        # Sort by risk level (high > low) then by name
        def _sort_key(m: AtRiskModule) -> tuple[int, str]:
            level_map = {"critical": 0, "high": 1, "medium": 2, "low": 3, "none": 4}
            return (level_map.get(m.risk_level, 99), m.module)

        module_risks.sort(key=_sort_key)

        # Cap at 15
        if len(module_risks) > 15:
            module_risks = module_risks[:15]

        return PRRiskReport(
            overall_risk=overall_risk,
            risk_score=risk_score,
            module_risks=module_risks,
        )
