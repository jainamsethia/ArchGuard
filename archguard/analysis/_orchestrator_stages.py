"""Stage extraction for AnalysisOrchestrator."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from rich.console import Console

from archguard.analysis._orchestrator_utils import (
    _build_partial_result as _build_partial_result_fn,
)
from archguard.analysis._reinference import _run_reinference as _run_reinference_fn
from archguard.analysis._suppression_filter import (
    _filter_suppressed as _filter_suppressed_fn,
)
from archguard.analysis.layers import AnalysisResult, ViolationDetail

logger = logging.getLogger(__name__)


def _execute_l1_l2_concurrently(
    orchestrator: Any,
    py_files: list[Path],
    affected: Any,
    progress: Any,
    quiet: bool,
    metrics: Any,
    commit_sha: str,
) -> tuple[float, list[ViolationDetail], float, list[ViolationDetail], list[Any]]:
    with ThreadPoolExecutor(max_workers=2) as executor:
        desc1, desc2 = "Layer 1: Boundary Analysis...", "Layer 2: Coupling Analysis..."
        task1 = progress.add_task(desc1, total=None) if progress else None
        task2 = progress.add_task(desc2, total=None) if progress else None
        if not progress and not quiet:
            print(desc1)
            print(desc2)

        l1_failures: list[Any] = []
        l2_failures: list[Any] = []

        def run_l1() -> tuple[float, list[ViolationDetail]]:
            with metrics.time_layer("layer1"):
                from archguard.analysis._layer_runners import _run_layer1

                return _run_layer1(
                    orchestrator.repo_root,
                    orchestrator.contract,
                    py_files,
                    affected,
                    commit_sha,
                    l1_failures,
                )

        def run_l2() -> tuple[float, list[ViolationDetail]]:
            with metrics.time_layer("layer2"):
                from archguard.analysis._layer_runners import _run_layer2

                return _run_layer2(
                    orchestrator.repo_root,
                    orchestrator.contract,
                    affected,
                    commit_sha,
                    l2_failures,
                )

        f_l1, f_l2 = executor.submit(run_l1), executor.submit(run_l2)
        layer1, l1_viols = f_l1.result()
        if progress:
            progress.update(
                task1,
                description=f"[green][OK] Layer 1:[/green] {len(l1_viols)} violations",
            )
            progress.stop_task(task1)
        elif not quiet:
            print(f"[OK] Layer 1 complete ({len(l1_viols)} violations)")

        layer2, l2_viols = f_l2.result()
        if progress:
            progress.update(
                task2,
                description=f"[green][OK] Layer 2:[/green] {len(l2_viols)} violations",
            )
            progress.stop_task(task2)
        elif not quiet:
            print(f"[OK] Layer 2 complete ({len(l2_viols)} violations)")

        return layer1, l1_viols, layer2, l2_viols, l1_failures + l2_failures


def _handle_l1_l2_fail_fast(
    orchestrator: Any,
    layer1: float,
    layer2: float,
    fail_threshold: float,
    progress: Any,
    violations: list[ViolationDetail],
    affected: Any,
    rel_files: list[str],
    commit_sha: str,
    metrics: Any,
    unique_failures: list[Any],
    evaluate_fitness: Callable[[AnalysisResult], None],
) -> AnalysisResult | None:
    if layer1 >= fail_threshold or layer2 >= fail_threshold:
        if progress:
            progress.stop()
        layer_name = (
            "Layer 1 (Boundaries)" if layer1 >= fail_threshold else "Layer 2 (Coupling)"
        )
        score = layer1 if layer1 >= fail_threshold else layer2
        Console().print(
            f"[bold red][X] FAIL-FAST:[/bold red] {layer_name} score {score:.2f} exceeds fail threshold {fail_threshold}. Skipping remaining layers."
        )

        res = _build_partial_result_fn(
            orchestrator.repo_root,
            orchestrator.contract,
            _filter_suppressed_fn,
            layer1,
            layer2,
            0.0,
            0.0,
            ["semantic", "duplication"],
            violations,
            affected,
            rel_files,
            commit_sha,
            metrics.to_dict(),
            suppression_path=getattr(orchestrator, "suppression_path", None),
        )
        res.parse_failures = unique_failures
        res.partial_analysis = bool(unique_failures)
        evaluate_fitness(res)
        return res
    return None


def _run_layer_1_2(
    orchestrator: Any,
    py_files: list[Path],
    affected: Any,
    progress: Any,
    quiet: bool,
    fail_fast: bool,
    evaluate_fitness: Callable[[AnalysisResult], None],
    metrics: Any,
    commit_sha: str,
    rel_files: list[str],
) -> tuple[list[ViolationDetail], float, float, list[Any], AnalysisResult | None]:
    fail_threshold = float(orchestrator.contract.get("fail_threshold", 0.75))
    layer1, l1_viols, layer2, l2_viols, parse_failures = _execute_l1_l2_concurrently(
        orchestrator, py_files, affected, progress, quiet, metrics, commit_sha
    )

    violations = l1_viols + l2_viols

    # Layer 1 only enforces the allowed_imports / disallowed_imports a contract
    # declares. Auto-generated contracts declare neither, so the layer inspects
    # every import and can never flag one -- a guaranteed 0.00 that reads as
    # "boundaries checked, all clean". Record that it had no rules to enforce,
    # so it is reported as not-applicable rather than as a clean pass.
    if not any(
        "disallowed_imports" in m or "allowed_imports" in m
        for m in orchestrator.contract.get("modules", [])
    ):
        metrics.extra["layer1_skipped"] = True
        metrics.extra["layer1_skip_reason"] = (
            "no import rules declared in this contract - no boundaries to enforce"
        )

    unique_failures = []
    seen = set()
    for f in parse_failures:
        key = (str(f.file_path), f.error_type)
        if key not in seen:
            seen.add(key)
            unique_failures.append(f)

    if unique_failures and orchestrator._audit:
        for f in unique_failures:
            orchestrator._audit.log(
                "parse_failure",
                file=str(f.file_path),
                error_type=f.error_type,
                error_message=f.error_message,
                is_critical=f.is_critical,
            )

    if fail_fast:
        res = _handle_l1_l2_fail_fast(
            orchestrator,
            layer1,
            layer2,
            fail_threshold,
            progress,
            violations,
            affected,
            rel_files,
            commit_sha,
            metrics,
            unique_failures,
            evaluate_fitness,
        )
        if res:
            return violations, layer1, layer2, unique_failures, res

    return violations, layer1, layer2, unique_failures, None


def _execute_layer_3(
    orchestrator: Any,
    py_files: list[Path],
    affected: Any,
    progress: Any,
    quiet: bool,
    metrics: Any,
    commit_sha: str,
) -> tuple[float, dict[str, float], list[ViolationDetail]]:
    desc3 = "Layer 3: Semantic Cohesion..."
    task3 = progress.add_task(desc3, total=None) if progress else None
    if not progress and not quiet:
        print(desc3)

    try:
        with metrics.time_layer("layer3"):
            from archguard.analysis._layer_runners import _run_layer3

            layer3, module_drifts, l3_viols, l3_skip_reason = _run_layer3(
                orchestrator.cache,
                orchestrator.contract,
                affected,
                py_files,
                commit_sha,
                orchestrator.repo_root,
            )
        if l3_skip_reason:
            metrics.extra["layer3_skipped"] = True
            metrics.extra["layer3_skip_reason"] = l3_skip_reason
            if progress:
                progress.update(
                    task3,
                    description=f"[yellow][!] {l3_skip_reason}[/yellow]",
                )
                progress.stop_task(task3)
            elif not quiet:
                print(f"[WARN] {l3_skip_reason}")
        else:
            if progress:
                progress.update(
                    task3,
                    description=f"[green][OK] Layer 3:[/green] {len(l3_viols)} violations",
                )
                progress.stop_task(task3)
            elif not quiet:
                print(f"[OK] Layer 3 complete ({len(l3_viols)} violations)")
        return layer3, module_drifts, l3_viols
    except Exception as e:
        if progress:
            progress.stop_task(task3)
        raise RuntimeError(f"Layer 3 analysis failed: {e}") from e


def _handle_l3_fail_fast(
    orchestrator: Any,
    layer1: float,
    layer2: float,
    layer3: float,
    fail_threshold: float,
    progress: Any,
    violations: list[ViolationDetail],
    affected: Any,
    rel_files: list[str],
    commit_sha: str,
    metrics: Any,
    unique_failures: list[Any],
    evaluate_fitness: Callable[[AnalysisResult], None],
) -> AnalysisResult | None:
    if layer3 >= fail_threshold:
        if progress:
            progress.stop()
        Console().print(
            f"[bold red][X] FAIL-FAST:[/bold red] Layer 3 (Semantic) score {layer3:.2f} exceeds fail threshold {fail_threshold}. Skipping remaining layers."
        )
        res = _build_partial_result_fn(
            orchestrator.repo_root,
            orchestrator.contract,
            _filter_suppressed_fn,
            layer1,
            layer2,
            layer3,
            0.0,
            ["duplication"],
            violations,
            affected,
            rel_files,
            commit_sha,
            metrics.to_dict(),
            suppression_path=getattr(orchestrator, "suppression_path", None),
        )
        res.parse_failures = unique_failures
        res.partial_analysis = bool(unique_failures)
        evaluate_fitness(res)
        return res
    return None


def _run_layer_3(
    orchestrator: Any,
    py_files: list[Path],
    violations: list[ViolationDetail],
    affected: Any,
    progress: Any,
    quiet: bool,
    fail_fast: bool,
    evaluate_fitness: Callable[[AnalysisResult], None],
    metrics: Any,
    commit_sha: str,
    rel_files: list[str],
    layer1: float,
    layer2: float,
    unique_failures: list[Any],
) -> tuple[list[ViolationDetail], float, AnalysisResult | None]:
    fail_threshold = float(orchestrator.contract.get("fail_threshold", 0.75))
    skip_layers = list(orchestrator.contract.get("skip_layers", []))
    SKIP_ML = os.getenv("ARCHGUARD_SKIP_ML", "").lower() in ("1", "true", "yes")
    if SKIP_ML and "semantic" not in skip_layers:
        skip_layers.append("semantic")
    if SKIP_ML and "duplication" not in skip_layers:
        skip_layers.append("duplication")

    module_drifts: dict[str, float] = {}
    if "semantic" in skip_layers:
        layer3 = 0.0
        # Record the skip, not just the 0.0. Without this the layer reports
        # "checked, no drift" to every consumer while it never ran at all.
        metrics.extra["layer3_skipped"] = True
        metrics.extra["layer3_skip_reason"] = (
            "semantic drift not run (ARCHGUARD_SKIP_ML or contract skip_layers)"
        )
        if progress:
            t = progress.add_task("Layer 3: Semantic Cohesion...", total=None)
            progress.update(
                t, description="[yellow][!] Layer 3: Skipped (config)[/yellow]"
            )
            progress.stop_task(t)
        elif not quiet:
            print("[WARN] Layer 3 Skipped (config)")
    else:
        layer3, module_drifts, l3_viols = _execute_layer_3(
            orchestrator, py_files, affected, progress, quiet, metrics, commit_sha
        )
        violations.extend(l3_viols)

    if fail_fast:
        res = _handle_l3_fail_fast(
            orchestrator,
            layer1,
            layer2,
            layer3,
            fail_threshold,
            progress,
            violations,
            affected,
            rel_files,
            commit_sha,
            metrics,
            unique_failures,
            evaluate_fitness,
        )
        if res:
            return violations, layer3, res

    _run_reinference_fn(
        orchestrator.repo_root,
        orchestrator.cache,
        orchestrator._audit,
        orchestrator.contract,
        affected,
        commit_sha,
        drift_results=module_drifts,
    )
    return violations, layer3, None

