import re
from pathlib import Path
from typing import List, Any, Optional, Set, Dict, Tuple

from archguard.analysis._models import AnalysisResult
from archguard.fitness.result import FitnessFunctionResult
from archguard.analysis.parser import ImportParser, ImportEdge
from archguard.analysis.layers import _get_module_paths
from archguard.analysis.coupling import compute_fan_out, compute_fan_in, _assign_file_to_module


class FitnessFunctionEvaluator:
    """Evaluates architectural fitness functions.
    
    Uses both the AnalysisResult (for health/debt metrics) and a direct graph 
    analysis (to accurately evaluate coupling, cycles, and imports without 
    relying on fragile string parsing of violation messages).
    """

    def __init__(self, repo_root: Optional[Path] = None, contract: Optional[dict[str, Any]] = None):
        self.repo_root = repo_root
        self.contract = contract or {}
        self._parsed_edges: List[ImportEdge] = []
        self._module_paths: Dict[str, List[str]] = {}
        self._graph_prepared = False

    def _prepare_graph(self):
        if self._graph_prepared:
            return
        self._graph_prepared = True
        
        if not self.repo_root or not self.contract:
            return

        modules_cfg = self.contract.get("modules", [])
        self._module_paths = {m["name"]: _get_module_paths(m) for m in modules_cfg}
        
        parser = ImportParser()
        parse_result = parser.parse_repo(self.repo_root, self._module_paths, allow_partial=True)
        self._parsed_edges = parse_result.edges

    def _get_module_dependencies(self) -> Dict[str, Set[str]]:
        self._prepare_graph()
        deps: Dict[str, Set[str]] = {m: set() for m in self._module_paths}
        
        for edge in self._parsed_edges:
            if edge.is_stdlib or edge.is_relative:
                continue
            
            src_mod = _assign_file_to_module(edge.source_file, self._module_paths)
            if not src_mod:
                continue
                
            import_as_path = edge.imported_module.replace(".", "/")
            tgt_mod = None
            for mod_name, paths in self._module_paths.items():
                if any(import_as_path.startswith(p) or p.startswith(import_as_path) for p in paths):
                    tgt_mod = mod_name
                    break
            
            if tgt_mod and tgt_mod != src_mod:
                deps[src_mod].add(tgt_mod)
                
        return deps

    def _has_cycles(self) -> Tuple[bool, str]:
        deps = self._get_module_dependencies()
        visited = set()
        rec_stack = set()
        
        def dfs(node, path):
            visited.add(node)
            rec_stack.add(node)
            path.append(node)
            
            for neighbor in deps.get(node, []):
                if neighbor not in visited:
                    if dfs(neighbor, path):
                        return True
                elif neighbor in rec_stack:
                    path.append(neighbor)
                    return True
                    
            rec_stack.remove(node)
            path.pop()
            return False

        for node in deps:
            if node not in visited:
                path = []
                if dfs(node, path):
                    cycle_str = " -> ".join(path[path.index(path[-1]):])
                    return True, f"Cycle found: {cycle_str}"
        return False, ""

    def evaluate(self, result: AnalysisResult, rules: List[str]) -> List[FitnessFunctionResult]:
        out = []
        for rule in rules:
            out.append(self._evaluate_rule(result, rule))
        return out

    def _evaluate_rule(self, result: AnalysisResult, rule: str) -> FitnessFunctionResult:
        rule_str = rule.strip()

        # 1. module[X] must not import module[Y]
        m = re.match(r"module\[(.*?)\] must not import module\[(.*?)\]", rule_str)
        if m:
            mod_x = m.group(1)
            mod_y = m.group(2)
            
            deps = self._get_module_dependencies()
            if mod_x in deps and mod_y in deps[mod_x]:
                return FitnessFunctionResult(
                    rule=rule_str,
                    passed=False,
                    details=f"Violation: {mod_x} imports {mod_y} directly."
                )
            return FitnessFunctionResult(
                rule=rule_str,
                passed=True,
                details=f"No direct import from {mod_x} to {mod_y} found."
            )

        # 2. graph.cycles == 0
        if rule_str == "graph.cycles == 0":
            has_cycle, cycle_path = self._has_cycles()
            if has_cycle:
                return FitnessFunctionResult(
                    rule=rule_str,
                    passed=False,
                    details=cycle_path
                )
            return FitnessFunctionResult(
                rule=rule_str,
                passed=True,
                details="No architectural cycles detected."
            )

        # 3. module[X].fan_out <= N
        m = re.match(r"module\[(.*?)\]\.fan_out <= (\d+)", rule_str)
        if m:
            mod_x = m.group(1)
            limit = int(m.group(2))
            
            self._prepare_graph()
            if mod_x not in self._module_paths:
                return FitnessFunctionResult(rule=rule_str, passed=False, error=f"Unknown module {mod_x}")
                
            fan_out = compute_fan_out(self._parsed_edges, mod_x, self._module_paths)
            passed = fan_out <= limit
            return FitnessFunctionResult(
                rule=rule_str,
                passed=passed,
                details=f"fan_out={fan_out}"
            )

        # 4. module[X].fan_in <= N
        m = re.match(r"module\[(.*?)\]\.fan_in <= (\d+)", rule_str)
        if m:
            mod_x = m.group(1)
            limit = int(m.group(2))
            
            self._prepare_graph()
            if mod_x not in self._module_paths:
                return FitnessFunctionResult(rule=rule_str, passed=False, error=f"Unknown module {mod_x}")
                
            fan_in = compute_fan_in(self._parsed_edges, mod_x, self._module_paths)
            passed = fan_in <= limit
            return FitnessFunctionResult(
                rule=rule_str,
                passed=passed,
                details=f"fan_in={fan_in}"
            )

        # 5. module[X].coupling_budget <= N
        m = re.match(r"module\[(.*?)\]\.coupling_budget <= (\d+)", rule_str)
        if m:
            mod_x = m.group(1)
            limit = int(m.group(2))
            
            for mod_cfg in self.contract.get("modules", []):
                if mod_cfg["name"] == mod_x:
                    budget = mod_cfg.get("coupling_budget", 3)
                    passed = budget <= limit
                    return FitnessFunctionResult(
                        rule=rule_str,
                        passed=passed,
                        details=f"budget={budget}"
                    )
            
            return FitnessFunctionResult(
                rule=rule_str,
                passed=False,
                error=f"budget data not found for {mod_x}."
            )

        # 6. health_score >= N
        m = re.match(r"health_score >= ([\d\.]+)", rule_str)
        if m:
            limit = float(m.group(1))
            val = result.archdebt.health_score
            return FitnessFunctionResult(
                rule=rule_str,
                passed=(val >= limit),
                details=f"health_score={val}"
            )

        # 7. layer[N].debt <= F
        m = re.match(r"layer\[(\d+)\]\.debt <= ([\d\.]+)", rule_str)
        if m:
            layer_idx = int(m.group(1))
            limit = float(m.group(2))
            val = None
            if layer_idx == 1:
                val = result.layer_scores.layer1_violation
            elif layer_idx == 2:
                val = result.layer_scores.layer2_coupling
            elif layer_idx == 3:
                val = result.layer_scores.layer3_drift
            elif layer_idx == 4:
                val = result.layer_scores.layer4_duplication

            if val is None:
                return FitnessFunctionResult(
                    rule=rule_str,
                    passed=False,
                    error=f"Invalid layer {layer_idx}"
                )
            return FitnessFunctionResult(
                rule=rule_str,
                passed=(val <= limit),
                details=f"layer{layer_idx}_score={val}"
            )

        # 8. violation_count <= N
        m = re.match(r"violation_count <= (\d+)", rule_str)
        if m:
            limit = int(m.group(1))
            val = len(result.violations)
            return FitnessFunctionResult(
                rule=rule_str,
                passed=(val <= limit),
                details=f"violation_count={val}"
            )

        # Unknown rule
        return FitnessFunctionResult(
            rule=rule_str,
            passed=False,
            error="Unknown rule syntax."
        )
