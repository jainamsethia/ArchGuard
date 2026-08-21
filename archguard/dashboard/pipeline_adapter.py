"""Adapter that runs the ArchGuard 4-layer pipeline against a cloned repository.

Key design decisions:
- AnalysisOrchestrator.run() is synchronous and blocking; we wrap it in
  asyncio.to_thread(...) to avoid blocking FastAPI. to_thread rather than
  loop.run_in_executor(None, ...): both use the same default executor, but only
  to_thread copies the context across the boundary, which is what lets the
  correlation id reach the log records the analysis itself emits.
- If .archguard.yml is absent, archguard.contract.generation.generate_contract()
  produces one headlessly and reports whether the module boundaries were
  measured from co-change history or guessed from directory names.
- 'changed_files' for a fresh clone = all .py files in the repo, since every
  file is "new" from the pipeline's perspective on a fresh workspace.
- The workspace clone is blobless but retains full history (see
  dashboard/workspace.py), so contract generation detects modules from real
  co-change data rather than falling back to directory names.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from archguard.contract.generation import ContractGenerationResult

logger = logging.getLogger(__name__)

ANALYSIS_TIMEOUT_SECONDS: int = int(os.environ.get("ARCHGUARD_ANALYSIS_TIMEOUT", "600"))

# Threshold policy for contracts the dashboard auto-generates for one-off
# analysis of an arbitrary repository. "ci" is the balanced preset (coupling
# <= 10, health >= 75); "strict" (coupling <= 5) flags healthy libraries, and
# "lenient" (coupling <= 15) lets genuinely tangled ones through. See
# archguard.profiles.defaults.PROFILES.
DASHBOARD_THRESHOLD_PROFILE: str = os.environ.get(
    "ARCHGUARD_DASHBOARD_PROFILE", "ci"
)

# --------------------------------------------------------------------------
# Result dataclasses (JSON-serializable)
# --------------------------------------------------------------------------

@dataclass
class LayerResult:
    layer: int
    name: str
    score: float
    violation_count: int
    skipped: bool = False
    skip_reason: str = ""

@dataclass
class AnalysisJobResult:
    job_id: str
    repo_url: str
    health_score: float             # 0.0–100.0 (higher = healthier)
    health_grade: str               # A / B / C / D / F
    composite_score: float          # 0.0–1.0 raw arch debt (lower = better)
    layer_results: list[LayerResult] = field(default_factory=list)
    total_violations: int = 0
    modules_analyzed: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    contract_auto_generated: bool = False
    fallback_directory_heuristic: bool = False
    fallback_reason: str = ""
    skipped: bool = False
    skip_reason: str = ""
    error: str | None = None

# --------------------------------------------------------------------------
# Public interface
# --------------------------------------------------------------------------

#: (message, phase) -> awaitable. The phase is optional because plenty of
#: messages describe something happening *within* a phase rather than a
#: transition into one.
ProgressCallback = Callable[[str, "str | None"], Awaitable[None]]

def _dashboard_suppression_path(repo_url: str) -> Path | None:
    """The durable, repository-keyed suppression file for *repo_url*.

    Returns None when the repository cannot be identified, in which case the
    analysis falls back to the default location inside the analysed tree.
    """
    if not repo_url:
        return None
    try:
        from archguard.dashboard.routes.suppression import suppression_base_dir
        from archguard.suppression.scope import suppression_path_for_repo

        return suppression_path_for_repo(suppression_base_dir(), repo_url)
    except Exception as exc:
        logger.warning(
            "Could not resolve suppression store for %s (%s); "
            "suppressions will not be applied to this run.", repo_url, exc,
        )
        return None


def _collect_python_files(repo_path: Path) -> list[Path]:
    """Every analysable ``.py`` file in the clone. Blocking; run in a thread."""
    from archguard.utils.paths import is_vendored

    return [
        f
        for f in repo_path.rglob("*.py")
        if not is_vendored(f, repo_path)
        and not any(part.startswith(".") for part in f.relative_to(repo_path).parts)
    ]


async def _persist(job_id: str, payload: dict[str, Any], commit_sha: str | None = None,
                   health_grade: str | None = None,
                   composite_score: float | None = None) -> None:
    """Store a run. Never raises: a persistence failure must not also lose the
    analysis, but it does mean the dashboard shows nothing for this job, so it
    is logged with a traceback rather than a bare warning."""
    try:
        from archguard.db.session import session_scope
        from archguard.db.store import persist_run

        async with session_scope() as session:
            await persist_run(
                session, job_id, payload,
                commit_sha=commit_sha,
                health_grade=health_grade,
                composite_score=composite_score,
            )
    except Exception:
        logger.exception(
            "[job %s] Failed to persist the analysis run; the dashboard will "
            "show no data for this job even though the analysis completed",
            job_id,
        )


def _skip_payload(reason: str, message: str) -> dict[str, Any]:
    """A run that produced no findings, recorded honestly rather than dropped.

    A job with no persisted run is indistinguishable from a job that never
    happened, and the read endpoints would report "no data" with no reason.
    """
    return {
        "job_id": "",
        "score": 0.0,
        "band": "FAIL",
        "violations": [],
        "skipped": True,
        "skip_reason": message,
        "layer_results": [],
        "module_scores": {},
        "modules_analyzed": [],
        "dependency_graph": {},
        "import_edges": [],
        "contract": {},
        "metrics": {"skip_detail": reason},
        "contract_auto_generated": False,
        "fallback_directory_heuristic": False,
        "fallback_reason": "",
        "derived_artifacts_error": "",
    }

async def run_analysis_on_repo(
    repo_path: Path,
    job_id: str,
    repo_url: str,
    progress_callback: ProgressCallback | None = None,
) -> AnalysisJobResult:
    """Run the full ArchGuard 4-layer pipeline against a cloned repo directory.

    If no .archguard.yml is found at repo_path, auto-generates one via
    generate_contract() before running analysis.

    Args:
        repo_path:           Path to the root of the cloned repository
        job_id:              UUID string for this job (used in result)
        repo_url:            Original GitHub URL (stored in result)
        progress_callback:   async callable(str, phase) for status messages

    ``skip_explanation`` is gone. It was threaded from here through
    ``AnalysisOrchestrator.run`` into ``_run_orchestrator`` and read by nothing
    (C6) -- L4 explanations lived in the CLI, which no longer exists. Keeping a
    parameter that describes behaviour the code does not have is how the
    startup banner came to promise operators a feature the website has never
    had.
    """

    async def _emit(msg: str, phase: str | None = None) -> None:
        if progress_callback:
            await progress_callback(msg, phase)
        logger.info("[job %s] %s", job_id, msg)

    start = time.monotonic()
    contract_auto_generated = False
    fallback_heuristic = False
    fallback_reason = ""

    # -- Step 1: Auto-generate contract if absent -------------------------
    archguard_yml = repo_path / ".archguard.yml"
    if not archguard_yml.exists():
        await _emit("No .archguard.yml found - generating contract...", "contract")
        try:
            generation = await asyncio.to_thread(_generate_contract_sync, repo_path)
            contract_auto_generated = True

            # Read straight off the result. This used to write the YAML, read it
            # back, and substring-match "fallback" in generated_by -- a round
            # trip that existed only because the CLI entry point returned None,
            # and whose failure mode was reporting a guessed module map as a
            # measured one.
            fallback_heuristic = generation.fallback_used
            fallback_reason = generation.fallback_reason

            if fallback_heuristic:
                await _emit(
                    "Contract auto-generated using the directory-name heuristic - "
                    "module boundaries are guessed, not measured."
                )
            else:
                await _emit(
                    f"Contract auto-generated: {generation.module_count} modules "
                    f"measured from {generation.commit_count} commits."
                )
        except Exception as exc:
            logger.warning("[job %s] Contract auto-generation failed: %s", job_id, exc)
            await _emit(f"Contract generation warning: {exc}. Attempting analysis anyway.")

    # -- Step 2: Collect all Python files (fresh clone = all files changed) --
    # In a thread: a recursive walk of an arbitrary cloned repository takes
    # seconds on a large tree, and it was running directly on the event loop,
    # stalling every other request on the server for its duration.
    py_files = await asyncio.to_thread(_collect_python_files, repo_path)

    if not py_files:
        elapsed = round(time.monotonic() - start, 1)
        await _persist(
            job_id,
            _skip_payload("no_python_files", "No Python files found in repository"),
        )
        return AnalysisJobResult(
            job_id=job_id, repo_url=repo_url,
            health_score=0.0, health_grade="F",
            composite_score=1.0, skipped=True,
            skip_reason="No Python files found in repository",
            duration_seconds=elapsed,
            contract_auto_generated=contract_auto_generated,
            fallback_directory_heuristic=fallback_heuristic,
            fallback_reason=fallback_reason,
        )

    await _emit(
        f"Found {len(py_files)} Python files. Starting 4-layer analysis...",
        "scanning",
    )

    # -- Step 3: Run analysis in thread pool -----------------------------
    try:
        # The orchestrator runs in a worker thread, so its callback cannot be
        # a coroutine. It publishes straight to the progress channel instead,
        # which is a synchronous Redis write -- the reason the phase messages
        # from inside the analysis can reach the browser at all, having
        # previously been dropped by `progress_callback=None`.
        loop = asyncio.get_running_loop()

        def _relay(message: str, phase: str | None = None) -> None:
            asyncio.run_coroutine_threadsafe(_emit(message, phase), loop)

        result, payload = await asyncio.wait_for(
            asyncio.to_thread(
                _run_analysis_sync,
                repo_path,
                py_files,
                job_id,
                repo_url,
                contract_auto_generated,
                fallback_heuristic,
                fallback_reason,
                _relay,
            ),
            timeout=ANALYSIS_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        # asyncio.TimeoutError is a subclass of Exception in 3.11+, caught here
        elapsed = round(time.monotonic() - start, 1)
        logger.exception("[job %s] Analysis failed", job_id)
        # This string reaches the browser. str(exc) on an arbitrary pipeline
        # failure carries server paths and internal module names, so only the
        # timeout -- a message this module composed -- is passed through.
        err_str = (
            f"Analysis timed out after {ANALYSIS_TIMEOUT_SECONDS}s"
            if isinstance(exc, TimeoutError)
            else "Analysis failed. See server logs for details."
        )
        await _persist(job_id, _skip_payload(f"{type(exc).__name__}: {exc}", err_str))
        return AnalysisJobResult(
            job_id=job_id, repo_url=repo_url,
            health_score=0.0, health_grade="F",
            composite_score=1.0,
            duration_seconds=elapsed,
            contract_auto_generated=contract_auto_generated,
            fallback_directory_heuristic=fallback_heuristic,
            fallback_reason=fallback_reason,
            error=err_str,
        )

    elapsed = round(time.monotonic() - start, 1)
    layer_results = _extract_layer_results(result)
    total_violations = len(result.violations)

    if payload is not None:
        await _persist(
            job_id,
            payload,
            commit_sha=result.commit_sha,
            health_grade=result.archdebt.health_grade,
            composite_score=result.archdebt.composite_score,
        )

    await _emit(
        f"Analysis complete in {elapsed}s. "
        f"Health: {result.archdebt.health_score:.1f}/100 ({result.archdebt.health_grade}). "
        f"Violations: {total_violations}."
    )

    return AnalysisJobResult(
        job_id=job_id,
        repo_url=repo_url,
        health_score=result.archdebt.health_score,
        health_grade=result.archdebt.health_grade,
        composite_score=result.archdebt.composite_score,
        layer_results=layer_results,
        total_violations=total_violations,
        modules_analyzed=list((payload or {}).get("modules_analyzed") or []),
        duration_seconds=elapsed,
        contract_auto_generated=contract_auto_generated,
        fallback_directory_heuristic=fallback_heuristic,
        fallback_reason=fallback_reason,
        skipped=result.skipped,
        skip_reason=result.skip_reason,
    )

# --------------------------------------------------------------------------
# Synchronous helpers (called via run_in_executor)
# --------------------------------------------------------------------------

def _generate_contract_sync(repo_path: Path) -> ContractGenerationResult:
    """Auto-generate .archguard.yml for a repository that ships none.

    Module boundaries come from real co-change history -- the workspace clone is
    blobless but keeps full history precisely so this does not degrade to the
    directory-name heuristic.

    Thresholds are pinned to a fixed profile rather than a baseline derived from
    the repository's own measured fan-out. That baseline sets each module's
    coupling_budget to ``ceil(fan_out * 1.5)`` of what was measured during
    generation, which is a sound "don't get worse than today" policy for a team
    enforcing the contract against their own future commits -- but here the
    contract is generated and graded in the same pass against a repository
    nobody will enforce it on. Under that baseline no repository can ever fail
    its own first scan, however badly coupled it actually is, so every dashboard
    run returned 100/A. See DASHBOARD_THRESHOLD_PROFILE.
    """
    from archguard.contract.generation import generate_contract

    return generate_contract(
        repo_root=repo_path,
        output=repo_path / ".archguard.yml",
        threshold_profile=DASHBOARD_THRESHOLD_PROFILE,
        # Allow community detection on a repository with minimal history.
        min_history_commits=1,
        # Layer 3 computes what it needs at analysis time; precomputing here
        # would pull the ML extras into whichever process runs this.
        compute_embeddings=False,
    )

def _run_analysis_sync(
    repo_path: Path,
    py_files: list[Path],
    job_id: str,
    repo_url: str = "",
    contract_auto_generated: bool = False,
    fallback_directory_heuristic: bool = False,
    fallback_reason: str = "",
    on_progress: Any = None,
) -> tuple[Any, dict[str, Any] | None]:
    """Run AnalysisOrchestrator synchronously. Called from a thread pool.

    Returns ``(result, payload)``. The payload is *returned* rather than
    written: persistence is async now, and the async engine belongs on the
    event loop, not in a worker thread. A caller that gets ``None`` back knows
    the run produced nothing storable and can say so.
    """
    from archguard.analysis.layers import AnalysisOrchestrator

    commit_sha = AnalysisOrchestrator.get_commit_sha(repo_path)
    # Point the suppression filter at the durable, repository-keyed store the
    # dashboard writes to. Without this it looks inside repo_path -- a throwaway
    # clone that no user has ever suppressed anything in -- so every suppressed
    # violation reappeared on the next scan.
    orchestrator = AnalysisOrchestrator(
        repo_root=repo_path,
        suppression_path=_dashboard_suppression_path(repo_url),
    )

    with orchestrator:
        result = orchestrator.run(
            changed_files=py_files,
            commit_sha=commit_sha,
            progress_callback=on_progress,
            fail_fast=False,
            # quiet=False so the callback above is actually called. It was True
            # with a None callback, which is why every per-layer message the
            # orchestrator emits was thrown away and the stream showed only the
            # four the adapter itself produced.
            quiet=on_progress is None,
        )

    # -- Build the persistable payload --
    try:
        from archguard.analysis._orchestrator_utils import _get_module_paths
        from archguard.analysis.coupling import _assign_file_to_module
        from archguard.analysis.parser import ImportParser
        from archguard.contract.loader import load_contract
        from archguard.dashboard._result_schema import (
            AnalysisResultPayload,
            LayerResultPayload,
            ViolationPayload,
        )
        from archguard.utils.paths import path_belongs_to_module

        band_val = str(result.archdebt.band.name).upper()
        audit_band = (
            "PASS"
            if band_val == "HEALTHY"
            else ("WATCH" if band_val == "WATCH"
            else ("WARN" if band_val == "WARN" else "FAIL"))
        )

        v_list_out = []
        for v in result.violations:
            raw_file = getattr(v, "file_path", "") or None
            line = getattr(v, "line", 0)

            from archguard.utils.severity import Severity

            scope = "file" if raw_file else "module"
            v_list_out.append(
                ViolationPayload(
                    file=raw_file,
                    line=line,
                    module=getattr(v, "module", None),
                    severity=getattr(v, "severity", Severity.LOW).value,
                    message=getattr(v, "message", ""),
                    layer=str(getattr(v, "layer", "0")),
                    scope=scope,
                    kind=getattr(v, "kind", "") or "",
                    metrics=dict(getattr(v, "metrics", {}) or {}),
                )
            )

        # -- Compute module_scores from violations --
        severity_weights = {"critical": 10, "high": 5, "medium": 2, "low": 1}
        module_penalty: dict[str, float] = {}
        for vp in v_list_out:
            mod = vp.module or "unknown"
            module_penalty[mod] = module_penalty.get(mod, 0) + severity_weights.get(vp.severity, 1)

        # Load contract for module list and dependency graph
        contract_dict: dict[str, Any] = {}
        modules_analyzed_list: list[str] = []
        dep_graph: dict[str, list[str]] = {}
        import_edges_list: list[dict[str, str]] = []
        derived_artifacts_error = ""

        try:
            contract_dict = load_contract(repo_path)
            modules_cfg = contract_dict.get("modules", [])
            module_names = [m["name"] for m in modules_cfg]
            modules_analyzed_list = module_names
            module_paths = {m["name"]: _get_module_paths(m) for m in modules_cfg}

            # Per-module health: 100 - penalty, clamped to [0, 100]
            module_scores: dict[str, float] = {}
            for name in module_names:
                penalty = module_penalty.get(name, 0)
                module_scores[name] = round(max(0.0, min(100.0, 100.0 - penalty * 3)), 1)

            # Dependency graph via FitnessFunctionEvaluator
            try:
                from archguard.fitness.evaluator import FitnessFunctionEvaluator
                evaluator = FitnessFunctionEvaluator(repo_path, contract_dict)
                dep_set = evaluator._get_module_dependencies()
                dep_graph = {k: list(v) for k, v in dep_set.items()}
                for name in module_paths:
                    dep_graph.setdefault(name, [])
            except Exception as dep_exc:
                logger.warning("Failed to compute dependency graph: %s", dep_exc)

            # Import edges
            try:
                parser = ImportParser()
                parse_result = parser.parse_repo(repo_path, module_paths, allow_partial=True)
                seen_edges: set[tuple[str, str]] = set()
                for e in parse_result.edges:
                    if e.is_stdlib or e.is_relative:
                        continue
                    importer = _assign_file_to_module(e.source_file, module_paths)
                    if not importer:
                        continue
                    import_as_path = e.imported_module.replace(".", "/")
                    imported = None
                    for tp_name, t_paths in module_paths.items():
                        targets_us = any(
                            path_belongs_to_module(import_as_path, [tp])
                            or path_belongs_to_module(tp, [import_as_path])
                            for tp in t_paths
                        )
                        if targets_us:
                            imported = tp_name
                            break
                    if not imported:
                        imported = e.imported_module.split(".")[0]
                    if importer != imported:
                        k = (importer, imported)
                        if k not in seen_edges:
                            seen_edges.add(k)
                            import_edges_list.append({"from": importer, "to": imported})
            except Exception as edge_exc:
                logger.warning("Failed to compute import edges: %s", edge_exc)

        except Exception as contract_exc:
            # The run is still persisted (the score and violations are real),
            # but every module-keyed artifact below is now empty. Record why so
            # the dashboard can distinguish that from a repo with no modules.
            logger.warning(
                "Failed to load contract for derived artifacts: %s", contract_exc,
                exc_info=True,
            )
            derived_artifacts_error = f"{type(contract_exc).__name__}: {contract_exc}"
            module_scores = {}

        payload = AnalysisResultPayload(
            job_id=job_id,
            score=result.archdebt.health_score,
            band=audit_band,
            violations=v_list_out,
            skipped=False,
            layer_results=[
                LayerResultPayload(
                    layer=lr.layer,
                    name=lr.name,
                    score=lr.score,
                    violation_count=lr.violation_count,
                    skipped=lr.skipped,
                    skip_reason=lr.skip_reason,
                )
                for lr in _extract_layer_results(result)
            ],
            module_scores=module_scores,
            modules_analyzed=modules_analyzed_list,
            dependency_graph=dep_graph,
            import_edges=import_edges_list,
            contract=contract_dict,
            metrics=result.metrics if isinstance(getattr(result, "metrics", None), dict) else {},
            contract_auto_generated=contract_auto_generated,
            fallback_directory_heuristic=fallback_directory_heuristic,
            fallback_reason=fallback_reason,
            derived_artifacts_error=derived_artifacts_error,
        )

        return result, payload.model_dump()
    except Exception:
        # The analysis still succeeded for the caller, but there is nothing to
        # store, so every read endpoint will report no data for this job. At
        # exception level with a traceback: a bare warning is how this stayed
        # invisible.
        logger.exception(
            "[job %s] Could not build the analysis payload; the dashboard will "
            "show no data for this job even though the analysis completed",
            job_id,
        )

    return result, None

def _extract_layer_results(result: Any) -> list[LayerResult]:
    """Convert AnalysisResult.layer_scores into a list of LayerResult.

    Per-layer skip state is read from ``result.metrics`` (where each stage
    records ``layer<N>_skipped`` / ``layer<N>_skip_reason``) rather than from the
    single run-level ``result.skip_reason``, which describes the whole analysis
    and was being attributed to whichever layer happened to be marked skipped.
    """
    ls = result.layer_scores
    layer_names = {
        1: "Import Boundary Violations",
        2: "Coupling Delta",
        3: "Semantic Drift",
        4: "Duplication / Explanation",
    }
    scores = {
        1: ls.layer1_violation,
        2: ls.layer2_coupling,
        3: ls.layer3_drift,
        4: ls.layer4_duplication,
    }
    metrics = getattr(result, "metrics", None) or {}
    skipped_names = getattr(result, "skipped_layers_names", []) or []

    layers: list[LayerResult] = []
    for n in (1, 2, 3, 4):
        skipped = bool(metrics.get(f"layer{n}_skipped")) or f"Layer {n}" in skipped_names
        reason = str(metrics.get(f"layer{n}_skip_reason", "") or "")
        if skipped and not reason:
            # Fall back to the run-level reason only when the stage recorded
            # none of its own (e.g. fail-fast skipped the layer outright).
            reason = getattr(result, "skip_reason", "") or ""
        layers.append(
            LayerResult(
                layer=n,
                name=layer_names[n],
                score=scores[n],
                violation_count=sum(1 for v in result.violations if v.layer == n),
                skipped=skipped,
                skip_reason=reason if skipped else "",
            )
        )
    return layers
