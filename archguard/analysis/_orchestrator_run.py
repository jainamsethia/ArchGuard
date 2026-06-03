"""Execution logic for AnalysisOrchestrator."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from archguard.analysis.scoring import compute_archdebt, LayerScores
from archguard.analysis.layers import AnalysisResult, ViolationDetail

logger = logging.getLogger(__name__)


def _run_orchestrator(
    orchestrator: Any,
    changed_files: list[Path],
    commit_sha: str,
    skip_explanation: bool = False,
    progress_callback: Any = None,
    fail_fast: bool = False,
    quiet: bool = False,
) -> AnalysisResult:
    """Run the full Layer 1–4 pipeline."""
    py_files = [f for f in changed_files if str(f).endswith(".py")]
    rel_files = [
        str(f.relative_to(orchestrator.repo_root)).replace("\\", "/")
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

    from archguard.analysis._orchestrator_utils import _get_affected_modules as _get_affected_modules_fn
    affected = _get_affected_modules_fn(orchestrator.repo_root, orchestrator.contract, py_files)
    violations: list[ViolationDetail] = []

    fail_threshold = float(orchestrator.contract.get("fail_threshold", 0.75))

    import sys

    is_tty = sys.stdout.isatty() and not quiet
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

        from archguard.observability.metrics import AnalysisMetrics

        metrics = AnalysisMetrics()

        with ThreadPoolExecutor(max_workers=2) as executor:
            desc1 = "Layer 1: Boundary Analysis..."
            desc2 = "Layer 2: Coupling Analysis..."

            if progress:
                task1 = progress.add_task(desc1, total=None)
                task2 = progress.add_task(desc2, total=None)
            else:
                if not quiet:
                    print(desc1)
                    print(desc2)

            l1_failures: list[Any] = []
            l2_failures: list[Any] = []

            def run_l1() -> tuple[float, list[ViolationDetail]]:
                with metrics.time_layer("layer1"):
                    from archguard.analysis._layer_runners import _run_layer1 as _run_l1
                    return _run_l1(
                        orchestrator.repo_root, orchestrator.contract, py_files, affected, commit_sha, l1_failures
                    )

            def run_l2() -> tuple[float, list[ViolationDetail]]:
                with metrics.time_layer("layer2"):
                    from archguard.analysis._layer_runners import _run_layer2 as _run_l2
                    return _run_l2(
                        orchestrator.repo_root, orchestrator.contract, affected, commit_sha, l2_failures
                    )

            future_l1 = executor.submit(run_l1)
            future_l2 = executor.submit(run_l2)

            layer1, l1_viols = future_l1.result()
            l1_violations = len(l1_viols)
            if progress:
                progress.update(
                    task1,
                    description=f"[green]✓ Layer 1:[/green] {l1_violations} violations",
                )
                progress.stop_task(task1)
            else:
                if not quiet:
                    print(f"✓ Layer 1 complete ({l1_violations} violations)")

            layer2, l2_viols = future_l2.result()
            l2_violations = len(l2_viols)
            if progress:
                progress.update(
                    task2,
                    description=f"[green]✓ Layer 2:[/green] {l2_violations} violations",
                )
                progress.stop_task(task2)
            else:
                if not quiet:
                    print(f"✓ Layer 2 complete ({l2_violations} violations)")
            
            violations.extend(l1_viols)
            violations.extend(l2_viols)

        elapsed = time.perf_counter() - start_time
        logger.debug(f"Layer 1 and 2 concurrent execution time: {elapsed:.2f}s")

        parse_failures = l1_failures + l2_failures
        # De-duplicate by file_path and error_type
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
            if layer1 >= fail_threshold:
                if progress:
                    progress.stop()
                from rich.console import Console

                Console().print(
                    f"[bold red]✗ FAIL-FAST:[/bold red] Layer 1 (Boundaries) score {layer1:.2f} "
                    f"exceeds fail threshold {fail_threshold}. Skipping remaining layers."
                )
                from archguard.analysis._orchestrator_utils import _build_partial_result as _build_partial_result_fn
                from archguard.analysis._suppression_filter import _filter_suppressed as _filter_suppressed_fn
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
                )
                res.parse_failures = unique_failures
                res.partial_analysis = bool(unique_failures)
                return res

            if layer2 >= fail_threshold:
                if progress:
                    progress.stop()
                from rich.console import Console

                Console().print(
                    f"[bold red]✗ FAIL-FAST:[/bold red] Layer 2 (Coupling) score {layer2:.2f} "
                    f"exceeds fail threshold {fail_threshold}. Skipping remaining layers."
                )
                from archguard.analysis._orchestrator_utils import _build_partial_result as _build_partial_result_fn
                from archguard.analysis._suppression_filter import _filter_suppressed as _filter_suppressed_fn
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
                )
                res.parse_failures = unique_failures
                res.partial_analysis = bool(unique_failures)
                return res

        # --- Layer 3: Semantic drift ---
        skip_layers = list(orchestrator.contract.get("skip_layers", []))
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
            if not quiet:
                print(desc3)

        if "semantic" in skip_layers:
            layer3 = 0.0
            module_drifts: dict[str, float] = {}
            if progress:
                progress.update(
                    task3,
                    description="[yellow]⚠ Layer 3: Skipped (config)[/yellow]",
                )
                progress.stop_task(task3)
            else:
                if not quiet:
                    print("⚠ Layer 3 Skipped (config)")
        else:
            try:
                with metrics.time_layer("layer3"):
                    from archguard.analysis._layer_runners import _run_layer3 as _run_l3
                    layer3, module_drifts, l3_viols = _run_l3(
                        orchestrator.cache, orchestrator.contract, affected, py_files, commit_sha, orchestrator.repo_root
                    )
                    violations.extend(l3_viols)
                l3_violations = len(l3_viols)
                if progress:
                    progress.update(
                        task3,
                        description=f"[green]✓ Layer 3:[/green] {l3_violations} violations",
                    )
                    progress.stop_task(task3)
                else:
                    if not quiet:
                        print(f"✓ Layer 3 complete ({l3_violations} violations)")
            except RuntimeError as e:
                if "ML dependencies" in str(e):
                    if progress:
                        progress.update(
                            task3,
                            description="[bold red]✗ Layer 3: Failed (Missing ML dependencies)[/bold red]",
                        )
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
            from archguard.analysis._orchestrator_utils import _build_partial_result as _build_partial_result_fn
            from archguard.analysis._suppression_filter import _filter_suppressed as _filter_suppressed_fn
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
            )
            res.parse_failures = unique_failures
            res.partial_analysis = bool(unique_failures)
            return res

        # --- Reinference: check staleness + create proposals ---
        from archguard.analysis._reinference import _run_reinference as _run_reinference_fn
        _run_reinference_fn(
            orchestrator.repo_root, orchestrator.cache, orchestrator._audit, orchestrator.contract,
            affected, commit_sha, drift_results=module_drifts,
        )

        # --- Layer 4: Duplication ---
        desc4 = "Layer 4: Duplication Detection..."
        if progress:
            task4 = progress.add_task(desc4, total=None)
        else:
            if not quiet:
                print(desc4)

        if "duplication" in skip_layers:
            layer4 = 0.0
            if progress:
                progress.update(
                    task4,
                    description="[yellow]⚠ Layer 4: Skipped (config)[/yellow]",
                )
                progress.stop_task(task4)
            else:
                if not quiet:
                    print("⚠ Layer 4 Skipped (config)")
        else:
            try:
                with metrics.time_layer("layer4"):
                    from archguard.analysis._layer_runners import _run_layer4 as _run_l4
                    layer4, l4_viols = _run_l4(
                        orchestrator.repo_root, orchestrator.cache, orchestrator.contract, affected, commit_sha
                    )
                    violations.extend(l4_viols)
                l4_violations = len(l4_viols)
                if progress:
                    progress.update(
                        task4,
                        description=f"[green]✓ Layer 4:[/green] {l4_violations} violations",
                    )
                    progress.stop_task(task4)
                else:
                    if not quiet:
                        print(f"✓ Layer 4 complete ({l4_violations} violations)")
            except RuntimeError as e:
                if "ML dependencies" in str(e):
                    if progress:
                        progress.update(
                            task4,
                            description="[bold red]✗ Layer 4: Failed (Missing ML dependencies)[/bold red]",
                        )
                        progress.stop_task(task4)
                    raise
                else:
                    raise

        # --- Filter out suppressed violations ---
        from archguard.analysis._suppression_filter import _filter_suppressed as _filter_suppressed_fn
        violations = _filter_suppressed_fn(orchestrator.repo_root, violations)

        scores = LayerScores(layer1, layer2, layer3, layer4)

        # Get weights from contract if available
        weights_cfg = orchestrator.contract.get("weights")
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
            fail_threshold=float(orchestrator.contract.get("fail_threshold", 0.75)),
            warn_threshold=float(orchestrator.contract.get("warn_threshold", 0.50)),
        )

        return AnalysisResult(
            archdebt=archdebt,
            violations=violations,
            layer_scores=scores,
            modules_analyzed=len(affected),
            changed_files=rel_files,
            commit_sha=commit_sha,
            metrics=metrics.to_dict(),
            parse_failures=unique_failures,
            partial_analysis=bool(unique_failures),
        )
    finally:
        if progress:
            progress.stop()
