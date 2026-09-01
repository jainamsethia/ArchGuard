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
#: ``(message, phase=None) -> None``. Spelled with ``...`` because
#: most call sites pass only the message: a phase names a transition
#: into a stage, and plenty of messages describe something happening
#: within one.
EmitFn = Callable[..., None]


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
        suppressed_hashes=getattr(orchestrator, "suppressed_hashes", None),
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
            ("Layer 2", "layer2_skipped"),
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

    # Every layer skipped means nothing was measured, and a run that measured
    # nothing is not a healthy run -- it is an unknown one. The composite has no
    # way to express that: it averages over the layers that ran, an average over
    # none of them is 0.00 debt, and 0.00 debt is 100/100 and a passing band. So
    # a repository whose contract matches no file came back perfect.
    #
    # Reported through `skipped`/`skip_reason`, which is what the product
    # already uses for a run it could not perform ("No Python files found") and
    # what the dashboard renders as a reason rather than as a grade.
    nothing_measured = len(skipped_names) == 4
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
        skipped=nothing_measured,
        skip_reason=(
            "No layer could measure this repository. Its contract declares no "
            "module that matches a file here, so there was nothing to analyse."
            if nothing_measured
            else ""
        ),
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

    emit("Evaluating fitness functions...", phase="fitness")

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
    progress_callback: Any = None,
    fail_fast: bool = False,
    quiet: bool = False,
    repo_files: list[Path] | None = None,
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

    # Layers 1, 2 and 4 measure the whole repository; only Layer 3 is scoped to
    # the slice this scan was given.
    #
    # The reason is cost, not principle. Layer 2 already parses every file in
    # the repository to build its edge list and then scored only the changed
    # modules, throwing the rest away -- so widening it costs a dict walk.
    # Layer 4 has been repository-wide since duplication stopped being carried
    # forward. Layer 1 is the only real addition, one AST pass on the thread
    # that is already blocked waiting for Layer 2's parse.
    #
    # What that buys is the property that matters: a score computed from a
    # slice is not comparable to one computed from a repository, and this
    # pipeline reported both under the same name. Editing a single file in a
    # module with no findings took a repository from 0.0/FAIL to 100.0/PASS
    # while still listing all four of its violations.
    #
    # Layer 3 keeps the slice because it is the one layer whose per-module cost
    # is a transformer forward pass rather than a parse. Its findings are
    # therefore the only ones carried forward -- see
    # cache.incremental.CARRYABLE_LAYERS.
    all_py_files = [
        f for f in (repo_files or changed_files) if str(f).endswith(".py")
    ]
    all_affected = _get_affected_modules_fn(
        orchestrator.repo_root, orchestrator.contract, all_py_files
    )

    def emit(message: str, phase: str | None = None) -> None:
        """Report a stage transition, and where in the run it happens.

        Always logged; forwarded to the caller's callback unless it asked to be
        quiet. This replaces a rich.Progress spinner and a scattering of bare
        print() calls, which assumed a terminal that a web request and a queue
        worker do not have.

        ``phase`` is what makes a determinate progress bar possible: the
        message alone is prose, and deriving a percentage from it would mean
        matching on strings that exist to be read by people.
        """
        logger.info("%s", message)
        if progress_callback is not None and not quiet:
            progress_callback(message, phase)

    def _eval_fitness(res: AnalysisResult) -> None:
        _evaluate_fitness_helper(orchestrator, res, emit)

    from archguard.observability.metrics import AnalysisMetrics

    metrics = AnalysisMetrics()

    v1_2, l1, l2, f_1_2, res1_2 = _run_layer_1_2(
        orchestrator,
        all_py_files,
        all_affected,
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
        orchestrator, v3, all_affected, emit, metrics, commit_sha
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
        all_affected,
        rel_files,
        f_1_2,
    )
