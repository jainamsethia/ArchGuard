"""Layer 1–4 analysis orchestration."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from archguard.analysis.scoring import (
    ArchDebtResult,
    LayerScores,
    compute_archdebt,
)
from archguard.utils.severity import Severity
from archguard.cache.db import EmbeddingDB
from archguard.cache.embeddings import EmbeddingCache
from archguard.contract.loader import load_contract

logger: logging.Logger = logging.getLogger(__name__)


@dataclass
class ViolationDetail:
    """A single violation found during analysis."""

    layer: int
    module: str
    message: str
    commit_sha: str
    file_path: str
    explanation: str = ""
    severity: Severity = Severity.LOW


@dataclass
class AnalysisResult:
    """Complete result of the analysis pipeline."""

    archdebt: ArchDebtResult
    violations: list[ViolationDetail] = field(default_factory=list)
    layer_scores: LayerScores = field(
        default_factory=lambda: LayerScores(0.0, 0.0, 0.0, 0.0),
    )
    modules_analyzed: int = 0
    changed_files: list[str] = field(default_factory=list)
    commit_sha: str = "unknown"
    skipped: bool = False
    skip_reason: str = ""
    fail_fast_triggered: bool = False
    skipped_layers_names: list[str] = field(default_factory=list)


from archguard.utils.paths import normalize_path, path_belongs_to_module


class AnalysisOrchestrator:
    """Orchestrates the full Layer 1–4 analysis pipeline."""

    def __init__(
        self,
        repo_root: Path,
        db_path: Path | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.contract: dict[str, Any] = load_contract(repo_root)
        db_path = db_path or repo_root / ".archguard-cache" / "embeddings.db"
        self.db = EmbeddingDB(db_path)
        self.cache = EmbeddingCache(self.db)
        self._audit: Any | None = None

    def run(
        self,
        changed_files: list[Path],
        commit_sha: str,
        skip_explanation: bool = False,
        progress_callback: Any = None,
        fail_fast: bool = False,
    ) -> AnalysisResult:
        """Run the full Layer 1–4 pipeline."""
        py_files = [f for f in changed_files if str(f).endswith(".py")]
        rel_files = [
            str(f.relative_to(self.repo_root)).replace("\\", "/")
            if f.is_absolute()
            else str(f).replace("\\", "/")
            for f in py_files
        ]

        if not py_files:
            scores = LayerScores(0.0, 0.0, 0.0, 0.0)
            return AnalysisResult(
                archdebt=compute_archdebt(scores),
                skipped=True,
                skip_reason="No Python files changed",
                commit_sha=commit_sha,
            )

        affected = self._get_affected_modules(py_files)
        violations: list[ViolationDetail] = []

        fail_threshold = float(self.contract.get("fail_threshold", 0.75))

        import sys
        is_tty = sys.stdout.isatty()
        progress = None
        if is_tty:
            from rich.progress import Progress, SpinnerColumn, TextColumn
            from rich.console import Console
            console = Console()
            progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True,
            )
            progress.start()

        try:
            import time
            from concurrent.futures import ThreadPoolExecutor

            start_time = time.perf_counter()

            with ThreadPoolExecutor(max_workers=2) as executor:
                desc1 = "Layer 1: Boundary Analysis..."
                desc2 = "Layer 2: Coupling Analysis..."
                
                if progress:
                    task1 = progress.add_task(desc1, total=None)
                    task2 = progress.add_task(desc2, total=None)
                else:
                    print(desc1)
                    print(desc2)
                    
                future_l1 = executor.submit(self._run_layer1, py_files, affected, commit_sha, violations)
                future_l2 = executor.submit(self._run_layer2, affected, commit_sha, violations)
                
                layer1 = future_l1.result()
                l1_violations = len([v for v in violations if v.layer == 1])
                if progress:
                    progress.update(task1, description=f"[green]✓ Layer 1:[/green] {l1_violations} violations")
                    progress.stop_task(task1)
                else:
                    print(f"✓ Layer 1 complete ({l1_violations} violations)")
                    
                layer2 = future_l2.result()
                l2_violations = len([v for v in violations if v.layer == 2])
                if progress:
                    progress.update(task2, description=f"[green]✓ Layer 2:[/green] {l2_violations} violations")
                    progress.stop_task(task2)
                else:
                    print(f"✓ Layer 2 complete ({l2_violations} violations)")

            elapsed = time.perf_counter() - start_time
            logger.debug(f"Layer 1 and 2 concurrent execution time: {elapsed:.2f}s")
            
            if fail_fast:
                if layer1 >= fail_threshold:
                    if progress:
                        progress.stop()
                    from rich.console import Console
                    Console().print(
                        f"[bold red]✗ FAIL-FAST:[/bold red] Layer 1 (Boundaries) score {layer1:.2f} "
                        f"exceeds fail threshold {fail_threshold}. Skipping remaining layers."
                    )
                    return self._build_partial_result(layer1, layer2, 0.0, 0.0, ["semantic", "duplication"], violations, affected, rel_files, commit_sha)
                
                if layer2 >= fail_threshold:
                    if progress:
                        progress.stop()
                    from rich.console import Console
                    Console().print(
                        f"[bold red]✗ FAIL-FAST:[/bold red] Layer 2 (Coupling) score {layer2:.2f} "
                        f"exceeds fail threshold {fail_threshold}. Skipping remaining layers."
                    )
                    return self._build_partial_result(layer1, layer2, 0.0, 0.0, ["semantic", "duplication"], violations, affected, rel_files, commit_sha)

            # --- Layer 3: Semantic drift ---
            skip_layers = list(self.contract.get("skip_layers", []))
            import os
            SKIP_ML = os.getenv("ARCHGUARD_SKIP_ML", "").lower() in ("1", "true", "yes")
            if SKIP_ML:
                if "semantic" not in skip_layers:
                    skip_layers.append("semantic")
                if "duplication" not in skip_layers:
                    skip_layers.append("duplication")
            
            desc3 = "Layer 3: Semantic Cohesion..."
            if progress:
                task3 = progress.add_task(desc3, total=None)
            else:
                print(desc3)
                
            if "semantic" in skip_layers:
                layer3 = 0.0
                module_drifts = {}
                if progress:
                    progress.update(task3, description="[yellow]⚠ Layer 3: Skipped (config)[/yellow]")
                    progress.stop_task(task3)
                else:
                    print("⚠ Layer 3 Skipped (config)")
            else:
                try:
                    layer3, module_drifts = self._run_layer3(affected, py_files, commit_sha, violations)
                    l3_violations = len([v for v in violations if v.layer == 3])
                    if progress:
                        progress.update(task3, description=f"[green]✓ Layer 3:[/green] {l3_violations} violations")
                        progress.stop_task(task3)
                    else:
                        print(f"✓ Layer 3 complete ({l3_violations} violations)")
                except RuntimeError as e:
                    if "ML dependencies" in str(e):
                        if progress:
                            progress.update(task3, description="[bold red]✗ Layer 3: Failed (Missing ML dependencies)[/bold red]")
                            progress.stop_task(task3)
                        raise
                    else:
                        raise

            if fail_fast and layer3 >= fail_threshold:
                if progress:
                    progress.stop()
                from rich.console import Console
                Console().print(
                    f"[bold red]✗ FAIL-FAST:[/bold red] Layer 3 (Semantic) score {layer3:.2f} "
                    f"exceeds fail threshold {fail_threshold}. Skipping remaining layers."
                )
                return self._build_partial_result(layer1, layer2, layer3, 0.0, ["duplication"], violations, affected, rel_files, commit_sha)

            # --- Reinference: check staleness + create proposals ---
            self._run_reinference(affected, commit_sha, drift_results=module_drifts)

            # --- Layer 4: Duplication ---
            desc4 = "Layer 4: Duplication Detection..."
            if progress:
                task4 = progress.add_task(desc4, total=None)
            else:
                print(desc4)
                
            if "duplication" in skip_layers:
                layer4 = 0.0
                if progress:
                    progress.update(task4, description="[yellow]⚠ Layer 4: Skipped (config)[/yellow]")
                    progress.stop_task(task4)
                else:
                    print("⚠ Layer 4 Skipped (config)")
            else:
                try:
                    layer4 = self._run_layer4(affected, commit_sha, violations)
                    l4_violations = len([v for v in violations if v.layer == 4])
                    if progress:
                        progress.update(task4, description=f"[green]✓ Layer 4:[/green] {l4_violations} violations")
                        progress.stop_task(task4)
                    else:
                        print(f"✓ Layer 4 complete ({l4_violations} violations)")
                except RuntimeError as e:
                    if "ML dependencies" in str(e):
                        if progress:
                            progress.update(task4, description="[bold red]✗ Layer 4: Failed (Missing ML dependencies)[/bold red]")
                            progress.stop_task(task4)
                        raise
                    else:
                        raise

            # --- Filter out suppressed violations ---
            violations = self._filter_suppressed(violations)

            scores = LayerScores(layer1, layer2, layer3, layer4)

            # Get weights from contract if available
            weights_cfg = self.contract.get("weights")
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
                fail_threshold=float(self.contract.get("fail_threshold", 0.75)),
                warn_threshold=float(self.contract.get("warn_threshold", 0.50)),
            )

            return AnalysisResult(
                archdebt=archdebt,
                violations=violations,
                layer_scores=scores,
                modules_analyzed=len(affected),
                changed_files=rel_files,
                commit_sha=commit_sha,
            )
        finally:
            if progress:
                progress.stop()

    # ------------------------------------------------------------------
    # Layer implementations
    # ------------------------------------------------------------------

    def _build_partial_result(
        self, l1: float, l2: float, l3: float, l4: float, 
        skipped_layers: list[str], 
        violations: list[ViolationDetail], 
        affected: dict, 
        rel_files: list[str], 
        commit_sha: str
    ) -> AnalysisResult:
        violations = self._filter_suppressed(violations)
        scores = LayerScores(l1, l2, l3, l4)
        
        weights_cfg = self.contract.get("weights")
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
            fail_threshold=float(self.contract.get("fail_threshold", 0.75)),
            warn_threshold=float(self.contract.get("warn_threshold", 0.50)),
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
        )
        res.fail_fast_triggered = True
        res.skipped_layers_names = skipped_layers
        return res

    def _run_layer1(
        self,
        py_files: list[Path],
        affected: dict[str, list[Path]],
        commit_sha: str,
        violations: list[ViolationDetail],
    ) -> float:
        """Layer 1: Import boundary violations."""
        from archguard.analysis.parser import ImportParser

        parser = ImportParser()
        modules_cfg = self.contract.get("modules", [])
        module_paths: dict[str, list[str]] = {
            m["name"]: m.get("paths", []) for m in modules_cfg
        }

        # Build disallowed/allowed maps
        disallowed_map: dict[str, set[str]] = {}
        allowed_map: dict[str, set[str]] = {}
        for m in modules_cfg:
            name = m["name"]
            if "disallowed_imports" in m:
                disallowed_map[name] = set(m["disallowed_imports"])
            if "allowed_imports" in m:
                allowed_map[name] = set(m["allowed_imports"])

        total_imports = 0
        violation_count = 0

        for fpath in py_files:
            try:
                source = fpath.read_text(errors="replace")
                rel = str(fpath.relative_to(self.repo_root)).replace("\\", "/")
                edges = parser.parse_file(source, rel, module_paths)

                # Determine which module this file belongs to
                file_module: str | None = None
                for mod_name, paths in module_paths.items():
                    for p in paths:
                        if path_belongs_to_module(rel, [p]):
                            file_module = mod_name
                            break
                    if file_module:
                        break

                if file_module is None:
                    continue

                for edge in edges:
                    if edge.is_stdlib or edge.is_relative:
                        continue
                    total_imports += 1
                    root = edge.imported_module.split(".")[0]

                    # Check disallowed
                    if file_module in disallowed_map:
                        if root in disallowed_map[file_module]:
                            violation_count += 1
                            violations.append(ViolationDetail(
                                layer=1,
                                module=file_module,
                                message=(
                                    f"Imports `{edge.imported_module}` "
                                    f"(disallowed)"
                                ),
                                commit_sha=commit_sha[:7],
                                file_path=rel,
                                severity=Severity.CRITICAL,
                            ))
                            continue

                    # Check allowed (if specified, only those are permitted)
                    if file_module in allowed_map:
                        if root not in allowed_map[file_module]:
                            # Check if it's within the same module
                            is_self = any(
                                path_belongs_to_module(root, [normalize_path(p).split("/")[0]])
                                for p in module_paths.get(file_module, [])
                            )
                            if not is_self:
                                violation_count += 1
                                violations.append(ViolationDetail(
                                    layer=1,
                                    module=file_module,
                                    message=(
                                        f"Imports `{edge.imported_module}` "
                                        f"(not in allowed_imports)"
                                    ),
                                    commit_sha=commit_sha[:7],
                                    file_path=rel,
                                    severity=Severity.CRITICAL,
                                ))

            except Exception as e:
                from archguard.utils.errors import AnalysisError
                raise AnalysisError(f"Layer 1 analysis failed on {fpath}", cause=e) from e

        return violation_count / max(total_imports, 1)

    def _run_layer2(
        self,
        affected: dict[str, list[Path]],
        commit_sha: str,
        violations: list[ViolationDetail],
    ) -> float:
        """Layer 2: Coupling delta."""
        from archguard.analysis.coupling import compute_coupling_delta, compute_fan_out
        from archguard.analysis.parser import ImportParser

        parser = ImportParser()
        modules_cfg = self.contract.get("modules", [])
        module_paths: dict[str, list[str]] = {
            m["name"]: m.get("paths", []) for m in modules_cfg
        }
        budgets: dict[str, int] = {
            m["name"]: m.get("coupling_budget", 3) for m in modules_cfg
        }

        edges = parser.parse_repo(self.repo_root, module_paths)
        max_delta = 0.0

        for mod_name in affected:
            if mod_name not in module_paths:
                continue
            fan_out = compute_fan_out(edges, mod_name, module_paths)
            budget = budgets.get(mod_name, 3)
            delta = compute_coupling_delta(fan_out, budget, mod_name)

            if delta > 0.0:
                violations.append(ViolationDetail(
                    layer=2,
                    module=mod_name,
                    message=f"fan_out={fan_out} exceeds budget={budget}",
                    commit_sha=commit_sha[:7],
                    file_path="",
                    severity=Severity.HIGH,
                ))

            max_delta = max(max_delta, delta)

        return max_delta

    def _run_layer3(
        self,
        affected: dict[str, list[Path]],
        py_files: list[Path],
        commit_sha: str,
        violations: list[ViolationDetail],
    ) -> tuple[float, dict[str, float]]:
        """Layer 3: Semantic drift."""
        from archguard.analysis.semantic import SemanticAnalyzer

        analyzer = SemanticAnalyzer(self.cache)
        modules_cfg = self.contract.get("modules", [])
        thresholds: dict[str, float] = {
            m["name"]: m.get("semantic_drift_threshold", 0.25)
            for m in modules_cfg
        }

        max_drift = 0.0
        module_drifts: dict[str, float] = {}
        for mod_name, files in affected.items():
            try:
                result = analyzer.compute_drift(mod_name, files, self.repo_root)
                module_drifts[mod_name] = result.drift_score
                if result.drift_score > thresholds.get(mod_name, 0.25):
                    violations.append(ViolationDetail(
                        layer=3,
                        module=mod_name,
                        message=(
                            f"semantic drift {result.drift_score:.2f} "
                            f"exceeds threshold "
                            f"{thresholds.get(mod_name, 0.25):.2f}"
                        ),
                        commit_sha=commit_sha[:7],
                        file_path="",
                        severity=Severity.LOW,
                    ))
                max_drift = max(max_drift, result.drift_score)
            except RuntimeError:
                raise
            except Exception as e:
                from archguard.utils.errors import AnalysisError
                raise AnalysisError(f"Layer 3 analysis failed on module {mod_name}", cause=e) from e

        return max_drift, module_drifts

    def _run_layer4(
        self,
        affected: dict[str, list[Path]],
        commit_sha: str,
        violations: list[ViolationDetail],
    ) -> float:
        """Layer 4: Duplication analysis."""
        from archguard.analysis.duplication import DuplicationAnalyzer

        analyzer = DuplicationAnalyzer(self.cache)
        modules_cfg = self.contract.get("modules", [])
        thresholds: dict[str, float] = {
            m["name"]: m.get("duplication_threshold", 0.5)
            for m in modules_cfg
        }
        max_agg = 0.0

        for mod_name, files in affected.items():
            rel_files = [
                str(f.relative_to(self.repo_root)).replace("\\", "/")
                if f.is_absolute()
                else str(f).replace("\\", "/")
                for f in files
            ]
            try:
                result = analyzer.analyze_module(mod_name, rel_files)
                if result.aggregate_score > 0.0 and not result.skipped:
                    # Collect file information from the matches
                    match_details = []
                    for m in result.matches[:3]: # limit to top 3 to avoid huge messages
                        src_file = m.source_function.split("::")[0]
                        tgt_file = m.matched_function.split("::")[0]
                        match_details.append(f"{src_file} <-> {tgt_file}")
                    
                    details_str = ", ".join(match_details)
                    if len(result.matches) > 3:
                        details_str += "..."

                    threshold = thresholds.get(mod_name, 0.5)
                    sev = Severity.MEDIUM if result.aggregate_score >= threshold else Severity.LOW

                    violations.append(ViolationDetail(
                        layer=4,
                        module=mod_name,
                        message=(
                            f"duplication score {result.aggregate_score:.2f} "
                            f"(matches found in: {details_str})"
                        ),
                        commit_sha=commit_sha[:7],
                        file_path="",
                        severity=sev,
                    ))
                max_agg = max(max_agg, result.aggregate_score)
            except RuntimeError:
                raise
            except Exception as e:
                from archguard.utils.errors import AnalysisError
                raise AnalysisError(f"Layer 4 analysis failed on module {mod_name}", cause=e) from e

        return max_agg

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _filter_suppressed(
        self,
        violations: list[ViolationDetail],
    ) -> list[ViolationDetail]:
        """Remove violations that match an active suppression."""
        try:
            from archguard.suppression.store import SuppressionStore

            store = SuppressionStore(self.repo_root)
            return [
                v for v in violations
                if not store.is_suppressed(v.module, v.layer, v.message)
            ]
        except Exception as e:
            from archguard.utils.errors import AnalysisError
            raise AnalysisError("Failed to filter suppressed violations", cause=e) from e

    def _run_reinference(
        self,
        affected: dict[str, list[Path]],
        commit_sha: str,
        drift_results: dict[str, float] | None = None,
    ) -> None:
        """Run reinference staleness check and create proposals if needed."""
        try:
            from archguard.contract.reinference import ReinferenceEngine

            engine = ReinferenceEngine(
                self.repo_root, audit_logger=self._audit,
            )
            engine.check_staleness()

            # Check each affected module's drift result
            modules_cfg = self.contract.get("modules", [])
            thresholds: dict[str, float] = {
                m["name"]: m.get("semantic_drift_threshold", 0.25)
                for m in modules_cfg
            }
            budgets: dict[str, int] = {
                m["name"]: m.get("coupling_budget", 3)
                for m in modules_cfg
            }
            module_paths: dict[str, list[str]] = {
                m["name"]: m.get("paths", []) for m in modules_cfg
            }

            for mod_name in affected:
                threshold = thresholds.get(mod_name, 0.25)
                
                if drift_results is not None and mod_name in drift_results:
                    drift_score = drift_results[mod_name]
                else:
                    # We need the drift score — recompute from semantic analyzer
                    from archguard.analysis.semantic import SemanticAnalyzer
    
                    analyzer = SemanticAnalyzer(self.cache)
                    try:
                        drift_result = analyzer.compute_drift(
                            mod_name, affected[mod_name], self.repo_root,
                        )
                        drift_score = drift_result.drift_score
                    except RuntimeError as e:
                        if "ML dependencies" in str(e):
                            continue
                        raise
                    except Exception as e:
                        from archguard.utils.errors import AnalysisError
                        raise AnalysisError(f"Reinference proposal failed on module {mod_name}", cause=e) from e

                try:
                    if engine.should_propose(mod_name, drift_score, threshold):
                        engine.create_proposal(
                            module_name=mod_name,
                            semantic_drift=drift_score,
                            new_centroid_paths=module_paths.get(mod_name, []),
                            current_coupling_budget=budgets.get(mod_name, 3),
                            source_commit=commit_sha,
                        )
                except Exception as e:
                    from archguard.utils.errors import AnalysisError
                    raise AnalysisError(f"Reinference proposal failed on module {mod_name}", cause=e) from e
        except Exception as e:
            from archguard.utils.errors import AnalysisError
            raise AnalysisError("Reinference check failed", cause=e) from e

    def _get_affected_modules(
        self,
        changed_files: list[Path],
    ) -> dict[str, list[Path]]:
        """Map changed files to their module names using contract paths or module_names."""
        modules_cfg = self.contract.get("modules", [])
        result: dict[str, list[Path]] = {}

        def _resolve_module_name(fpath: Path) -> str:
            rel = fpath.relative_to(self.repo_root)
            parts = list(rel.with_suffix("").parts)
            if parts and parts[0] == "src":
                parts = parts[1:]
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            return ".".join(parts)

        for fpath in changed_files:
            rel = (
                str(fpath.relative_to(self.repo_root)).replace("\\", "/")
                if fpath.is_absolute()
                else str(fpath).replace("\\", "/")
            )
            dotted_module = _resolve_module_name(fpath if fpath.is_absolute() else self.repo_root / fpath)

            for mod in modules_cfg:
                mod_name: str = mod["name"]
                paths: list[str] = mod.get("paths", [])
                module_names: list[str] = mod.get("module_names", [])

                matched = False
                for p in paths:
                    if path_belongs_to_module(rel, [p]):
                        matched = True
                        break

                if not matched:
                    for m_name in module_names:
                        if dotted_module == m_name or dotted_module.startswith(m_name + "."):
                            matched = True
                            break

                if matched:
                    result.setdefault(mod_name, []).append(fpath)
                    break

        return result

    @staticmethod
    def get_commit_sha(repo_root: Path) -> str:
        """Read HEAD commit SHA, return 7-char short form."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                cwd=str(repo_root),
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()[:7]
        except Exception as e:
            from archguard.utils.errors import AnalysisError
            raise AnalysisError("Failed to get commit SHA", cause=e) from e
        return "unknown"
