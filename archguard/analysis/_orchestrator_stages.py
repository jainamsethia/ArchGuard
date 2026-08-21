"""Stage extraction for AnalysisOrchestrator."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from archguard.analysis._orchestrator_utils import (
    _build_partial_result as _build_partial_result_fn,
)
from archguard.analysis._reinference import _run_reinference as _run_reinference_fn
from archguard.analysis._suppression_filter import (
    _filter_suppressed as _filter_suppressed_fn,
)
from archguard.analysis.layers import AnalysisResult, ViolationDetail

logger = logging.getLogger(__name__)

#: See _orchestrator_run.EmitFn -- reports a stage transition.
#: ``(message, phase=None) -> None``. Spelled with ``...`` because
#: most call sites pass only the message: a phase names a transition
#: into a stage, and plenty of messages describe something happening
#: within one.
EmitFn = Callable[..., None]


def _execute_l1_l2_concurrently(
    orchestrator: Any,
    py_files: list[Path],
    affected: Any,
    emit: EmitFn,
    metrics: Any,
    commit_sha: str,
) -> tuple[float, list[ViolationDetail], float, list[ViolationDetail], list[Any]]:
    with ThreadPoolExecutor(max_workers=2) as executor:
        emit("Layer 1: import boundaries, Layer 2: coupling...", phase="layer1")

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
        emit(f"Layer 1 complete: {len(l1_viols)} violation(s).")

        layer2, l2_viols = f_l2.result()
        emit(f"Layer 2 complete: {len(l2_viols)} violation(s).", phase="layer2")

        return layer1, l1_viols, layer2, l2_viols, l1_failures + l2_failures


def _handle_l1_l2_fail_fast(
    orchestrator: Any,
    layer1: float,
    layer2: float,
    fail_threshold: float,
    emit: EmitFn,
    violations: list[ViolationDetail],
    affected: Any,
    rel_files: list[str],
    commit_sha: str,
    metrics: Any,
    unique_failures: list[Any],
    evaluate_fitness: Callable[[AnalysisResult], None],
) -> AnalysisResult | None:
    if layer1 >= fail_threshold or layer2 >= fail_threshold:
        layer_name = (
            "Layer 1 (Boundaries)" if layer1 >= fail_threshold else "Layer 2 (Coupling)"
        )
        score = layer1 if layer1 >= fail_threshold else layer2
        emit(
            f"FAIL-FAST: {layer_name} score {score:.2f} exceeds fail threshold "
            f"{fail_threshold}. Skipping remaining layers."
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
    emit: EmitFn,
    fail_fast: bool,
    evaluate_fitness: Callable[[AnalysisResult], None],
    metrics: Any,
    commit_sha: str,
    rel_files: list[str],
) -> tuple[list[ViolationDetail], float, float, list[Any], AnalysisResult | None]:
    fail_threshold = float(orchestrator.contract.get("fail_threshold", 0.75))
    layer1, l1_viols, layer2, l2_viols, parse_failures = _execute_l1_l2_concurrently(
        orchestrator, py_files, affected, emit, metrics, commit_sha
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
            emit,
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
    emit: EmitFn,
    metrics: Any,
    commit_sha: str,
) -> tuple[float, dict[str, float], list[ViolationDetail]]:
    emit("Layer 3: semantic cohesion...", phase="layer3")

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
            emit(f"Layer 3 skipped: {l3_skip_reason}")
        else:
            emit(f"Layer 3 complete: {len(l3_viols)} violation(s).")
        return layer3, module_drifts, l3_viols
    except Exception as e:
        raise RuntimeError(f"Layer 3 analysis failed: {e}") from e


def _handle_l3_fail_fast(
    orchestrator: Any,
    layer1: float,
    layer2: float,
    layer3: float,
    fail_threshold: float,
    emit: EmitFn,
    violations: list[ViolationDetail],
    affected: Any,
    rel_files: list[str],
    commit_sha: str,
    metrics: Any,
    unique_failures: list[Any],
    evaluate_fitness: Callable[[AnalysisResult], None],
) -> AnalysisResult | None:
    if layer3 >= fail_threshold:
        emit(
            f"FAIL-FAST: Layer 3 (Semantic) score {layer3:.2f} exceeds fail "
            f"threshold {fail_threshold}. Skipping remaining layers."
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
    emit: EmitFn,
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
        emit(
            "Layer 3 skipped (ARCHGUARD_SKIP_ML or contract skip_layers).",
            phase="layer3",
        )
    else:
        layer3, module_drifts, l3_viols = _execute_layer_3(
            orchestrator, py_files, affected, emit, metrics, commit_sha
        )
        violations.extend(l3_viols)

    if fail_fast:
        res = _handle_l3_fail_fast(
            orchestrator,
            layer1,
            layer2,
            layer3,
            fail_threshold,
            emit,
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

