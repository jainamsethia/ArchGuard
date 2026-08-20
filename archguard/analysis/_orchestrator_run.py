import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from archguard.analysis._models import AnalysisResult, ViolationDetail
from archguard.analysis._orchestrator_layer4 import _run_layer_4
from archguard.analysis._orchestrator_stages import _run_layer_1_2, _run_layer_3
from archguard.analysis._suppression_filter import _filter_suppressed as _filter_suppressed_fn
from archguard.analysis.scoring import LayerScores, compute_archdebt

logger = logging.getLogger(__name__)

#: Reports a stage transition. The analysis engine runs inside a web request, a
#: queue worker and a test; none of them have a terminal to draw a spinner on.
EmitFn = Callable[[str], None]


def _finalize_result(
    orchestrator: Any,
    violations: list[ViolationDetail],
    commit_sha: str,
    metrics: Any,
    evaluate_fitness: Callable[[AnalysisResult], None],
    layer1: float,
    layer2: float,
    layer3: float,
    layer4: float,
    affected: Any,
    rel_files: list[str],
    unique_failures: list[Any],
) -> AnalysisResult:
    violations = _filter_suppressed_fn(
        orchestrator.repo_root,
        violations,
        store_path=getattr(orchestrator, "suppression_path", None),
    )

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

    # A layer that did not run must not be averaged into the composite as a
    # 0.00. compute_archdebt reweights across the layers that remain, so the
    # score reflects what was actually measured rather than being diluted by
    # checks that had nothing to check. Layer 1 is included here because an
    # auto-generated contract declares no import rules for it to enforce.
    skipped_names = [
        name
        for name, extra_key in (
            ("Layer 1", "layer1_skipped"),
            ("Layer 3", "layer3_skipped"),
            ("Layer 4", "layer4_skipped"),
        )
        if metrics.extra.get(extra_key)
    ]

    archdebt = compute_archdebt(
        scores,
        weights=weights,
        fail_threshold=float(orchestrator.contract.get("fail_threshold", 0.75)),
        warn_threshold=float(orchestrator.contract.get("warn_threshold", 0.50)),
        skipped=skipped_names,
    )

    res = AnalysisResult(
        archdebt=archdebt,
        violations=violations,
        layer_scores=scores,
        modules_analyzed=len(affected),
        changed_files=rel_files,
        commit_sha=commit_sha,
        metrics=metrics.to_dict(),
        parse_failures=unique_failures,
        partial_analysis=bool(unique_failures),
        skipped_layers_names=skipped_names,
    )

    evaluate_fitness(res)
    return res


def _evaluate_fitness_helper(
    orchestrator: Any, res: AnalysisResult, emit: EmitFn
) -> None:
    from archguard.config import parse_fitness_functions
    from archguard.fitness.evaluator import FitnessFunctionEvaluator

    fitness_configs = parse_fitness_functions(orchestrator.contract)
    if not fitness_configs:
        return

    emit("Evaluating fitness functions...")

    evaluator = FitnessFunctionEvaluator(orchestrator.repo_root, orchestrator.contract)
    rules = [c.rule for c in fitness_configs]
    fitness_results = evaluator.evaluate(res, rules)
    res.archdebt.apply_fitness_results(fitness_results, fitness_configs)

    from archguard.audit.logger import serialize_fitness_results

    res.metrics["fitness_results"] = serialize_fitness_results(
        fitness_results, fitness_configs
    )

    failures = sum(1 for r in fitness_results if not getattr(r, "passed", True))
    emit(
        f"Fitness functions: {failures} failure(s)."
        if failures
        else "Fitness functions: all passed."
    )


def _run_orchestrator(
    orchestrator: Any,
    changed_files: list[Path],
    commit_sha: str,
    skip_explanation: bool = False,
    progress_callback: Any = None,
    fail_fast: bool = False,
    quiet: bool = False,
) -> AnalysisResult:
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

    from archguard.analysis._orchestrator_utils import (
        _get_affected_modules as _get_affected_modules_fn,
    )

    affected = _get_affected_modules_fn(
        orchestrator.repo_root, orchestrator.contract, py_files
    )

    def emit(message: str) -> None:
        """Report a stage transition.

        Always logged; forwarded to the caller's callback unless it asked to be
        quiet. This replaces a rich.Progress spinner and a scattering of bare
        print() calls, which assumed a terminal that a web request and a queue
        worker do not have. progress_callback was already threaded here from
        AnalysisOrchestrator.run() and simply never read.
        """
        logger.info("%s", message)
        if progress_callback is not None and not quiet:
            progress_callback(message)

    def _eval_fitness(res: AnalysisResult) -> None:
        _evaluate_fitness_helper(orchestrator, res, emit)

    from archguard.observability.metrics import AnalysisMetrics

    metrics = AnalysisMetrics()

    v1_2, l1, l2, f_1_2, res1_2 = _run_layer_1_2(
        orchestrator,
        py_files,
        affected,
        emit,
        fail_fast,
        _eval_fitness,
        metrics,
        commit_sha,
        rel_files,
    )
    if res1_2:
        return res1_2

    v3, l3, res3 = _run_layer_3(
        orchestrator,
        py_files,
        v1_2,
        affected,
        emit,
        fail_fast,
        _eval_fitness,
        metrics,
        commit_sha,
        rel_files,
        l1,
        l2,
        f_1_2,
    )
    if res3:
        return res3

    v4, l4 = _run_layer_4(
        orchestrator, v3, affected, emit, metrics, commit_sha
    )

    return _finalize_result(
        orchestrator,
        v4,
        commit_sha,
        metrics,
        _eval_fitness,
        l1,
        l2,
        l3,
        l4,
        affected,
        rel_files,
        f_1_2,
    )
