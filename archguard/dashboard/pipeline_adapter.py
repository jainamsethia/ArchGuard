"""Adapter that runs the ArchGuard 4-layer pipeline against a cloned repository.

Key design decisions:
- AnalysisOrchestrator.run() is synchronous and blocking; we wrap it in
  asyncio.get_running_loop().run_in_executor(None, ...) to avoid blocking FastAPI.
- If .archguard.yml is absent, we call _run_init_cli() programmatically
  with confirm_all=True and force_ci=True to auto-generate it.
- 'changed_files' for a fresh clone = all .py files in the repo (since every
  file is "new" from the pipeline's perspective with --depth=1).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
import os
from typing import Awaitable, Callable, Any

logger = logging.getLogger(__name__)

ANALYSIS_TIMEOUT_SECONDS: int = int(os.environ.get("ARCHGUARD_ANALYSIS_TIMEOUT", "600"))

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
    skipped: bool = False
    skip_reason: str = ""
    error: str | None = None

# --------------------------------------------------------------------------
# Public interface
# --------------------------------------------------------------------------

ProgressCallback = Callable[[str], Awaitable[None]]

def _log_skip_or_error(job_id: str, repo_path: Path, reason: str) -> None:
    try:
        from archguard.audit.logger import AuditLogger
        from archguard.config import AUDIT_EVENT_ANALYSIS
        audit = AuditLogger(log_path=repo_path / ".archguard-cache" / "audit.jsonl")
        audit.log(
            AUDIT_EVENT_ANALYSIS,
            job_id=job_id,
            score=0.0,
            band="FAIL",
            violations=[],
            skipped=True,
            error=reason
        )
    except Exception as exc:
        logger.warning("Failed to write audit log in pipeline adapter: %s", exc)

async def run_analysis_on_repo(
    repo_path: Path,
    job_id: str,
    repo_url: str,
    progress_callback: ProgressCallback | None = None,
    skip_explanation: bool = True,
) -> AnalysisJobResult:
    """Run the full ArchGuard 4-layer pipeline against a cloned repo directory.

    If no .archguard.yml is found at repo_path, auto-generates one via
    _run_init_cli() before running analysis.

    Args:
        repo_path:           Path to the root of the cloned repository
        job_id:              UUID string for this job (used in result)
        repo_url:            Original GitHub URL (stored in result)
        progress_callback:   async callable(str) invoked with status messages
        skip_explanation:    If True, skips L4 LLM explanation (faster, default)
    """
    async def _emit(msg: str) -> None:
        if progress_callback:
            await progress_callback(msg)
        logger.info("[job %s] %s", job_id, msg)

    start = time.monotonic()
    contract_auto_generated = False

    # -- Step 1: Auto-generate contract if absent -------------------------
    archguard_yml = repo_path / ".archguard.yml"
    if not archguard_yml.exists():
        await _emit("No .archguard.yml found - generating contract from directory structure...")
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, _generate_contract_sync, repo_path
            )
            contract_auto_generated = True
            await _emit("Contract auto-generated from directory structure.")
        except Exception as exc:
            logger.warning("[job %s] Contract auto-generation failed: %s", job_id, exc)
            await _emit(f"Contract generation warning: {exc}. Attempting analysis anyway.")

    # -- Step 2: Collect all Python files (fresh clone = all files changed) --
    py_files = list(repo_path.rglob("*.py"))
    # Exclude hidden dirs and __pycache__
    py_files = [
        f for f in py_files
        if not any(part.startswith(".") or part == "__pycache__" for part in f.relative_to(repo_path).parts)
    ]

    if not py_files:
        elapsed = round(time.monotonic() - start, 1)
        _log_skip_or_error(job_id, repo_path, reason="no_python_files")
        return AnalysisJobResult(
            job_id=job_id, repo_url=repo_url,
            health_score=0.0, health_grade="F",
            composite_score=1.0, skipped=True,
            skip_reason="No Python files found in repository",
            duration_seconds=elapsed,
            contract_auto_generated=contract_auto_generated,
        )

    await _emit(f"Found {len(py_files)} Python files. Starting 4-layer analysis...")

    # -- Step 3: Run analysis in thread pool -----------------------------
    try:
        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                _run_analysis_sync,
                repo_path,
                py_files,
                skip_explanation,
                job_id,
            ),
            timeout=ANALYSIS_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        # asyncio.TimeoutError is a subclass of Exception in 3.11+, caught here
        elapsed = round(time.monotonic() - start, 1)
        logger.exception("[job %s] Analysis failed", job_id)
        err_str = "Analysis timed out" if isinstance(exc, TimeoutError) else str(exc)
        _log_skip_or_error(job_id, repo_path, reason=err_str)
        return AnalysisJobResult(
            job_id=job_id, repo_url=repo_url,
            health_score=0.0, health_grade="F",
            composite_score=1.0,
            duration_seconds=elapsed,
            contract_auto_generated=contract_auto_generated,
            error=err_str,
        )

    elapsed = round(time.monotonic() - start, 1)
    layer_results = _extract_layer_results(result)
    total_violations = len(result.violations)

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
        modules_analyzed=[],  # populated in audit log; kept for API compat
        duration_seconds=elapsed,
        contract_auto_generated=contract_auto_generated,
        skipped=result.skipped,
        skip_reason=result.skip_reason,
    )

# --------------------------------------------------------------------------
# Synchronous helpers (called via run_in_executor)
# --------------------------------------------------------------------------

def _generate_contract_sync(repo_path: Path) -> None:
    """Auto-generate .archguard.yml using _run_init_cli with headless settings.

    Uses confirm_all=True (no interactive prompts) and force_ci=True (skip
    shallow-clone guard). This runs phases 1-5 of the init wizard using
    directory-structure-based community detection (commit history unavailable
    in a --depth=1 shallow clone).
    """
    import typer
    from rich.console import Console
    from archguard.cli._init_dispatch import _run_init_cli

    # Create a minimal Context to satisfy the Typer signature
    # _run_init_cli only uses ctx.obj.get("quiet") and ctx.obj.get("verbose")
    app = typer.Typer()
    @app.command()
    def fake() -> None: pass
    ctx = typer.Context(command=typer.main.get_command(app))
    ctx.obj = {"quiet": True, "verbose": False}

    _run_init_cli(
        ctx=ctx,
        repo_root=repo_path,
        output=repo_path / ".archguard.yml",
        confirm_all=True,
        force_ci=True,   # bypasses shallow-clone guard in CI check
        resume=False,
        no_llm=True,     # skip LLM-based contract inference (faster, offline-safe)
        min_history_commits=1,  # allow community detection with minimal history
        llm_init=False,
        wizard=False,
        force=True,
        _console=Console(quiet=True),
    )

def _run_analysis_sync(
    repo_path: Path,
    py_files: list[Path],
    skip_explanation: bool,
    job_id: str,
) -> Any:
    """Run AnalysisOrchestrator synchronously. Called from a thread pool."""
    from archguard.analysis.layers import AnalysisOrchestrator

    commit_sha = AnalysisOrchestrator.get_commit_sha(repo_path)
    orchestrator = AnalysisOrchestrator(repo_root=repo_path)

    with orchestrator:
        result = orchestrator.run(
            changed_files=py_files,
            commit_sha=commit_sha,
            skip_explanation=skip_explanation,
            progress_callback=None,  # SSE progress handled at the adapter level
            fail_fast=False,
            quiet=True,
        )

    # -- Log the result for the dashboard context --
    try:
        from archguard.audit.logger import AuditLogger
        from archguard.config import AUDIT_EVENT_ANALYSIS
        from archguard.dashboard._result_schema import AnalysisResultPayload, LayerResultPayload, ViolationPayload
        from archguard.contract.loader import load_contract
        from archguard.analysis._orchestrator_utils import _get_module_paths
        from archguard.analysis.parser import ImportParser
        from archguard.analysis.coupling import _assign_file_to_module
        from archguard.utils.paths import path_belongs_to_module

        audit = AuditLogger(log_path=repo_path / ".archguard-cache" / "audit.jsonl")
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
            logger.warning("Failed to load contract for derived artifacts: %s", contract_exc)
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
        )
            
        audit.log(
            AUDIT_EVENT_ANALYSIS,
            **payload.model_dump()
        )
    except Exception as exc:
        logger.warning("Failed to write audit log in pipeline adapter: %s", exc)

    return result

def _extract_layer_results(result: Any) -> list[LayerResult]:
    """Convert AnalysisResult.layer_scores into a list of LayerResult."""
    ls = result.layer_scores
    layer_names = {
        1: "Import Boundary Violations",
        2: "Coupling Delta",
        3: "Semantic Drift",
        4: "Duplication / Explanation",
    }

    layers = [
        LayerResult(layer=1, name=layer_names[1], score=ls.layer1_violation,
                    violation_count=sum(1 for v in result.violations if v.layer == 1)),
        LayerResult(layer=2, name=layer_names[2], score=ls.layer2_coupling,
                    violation_count=sum(1 for v in result.violations if v.layer == 2)),
        LayerResult(layer=3, name=layer_names[3], score=ls.layer3_drift,
                    violation_count=sum(1 for v in result.violations if v.layer == 3),
                    skipped="Layer 3" in result.skipped_layers_names,
                    skip_reason=result.skip_reason if "Layer 3" in result.skipped_layers_names else ""),
        LayerResult(layer=4, name=layer_names[4], score=ls.layer4_duplication,
                    violation_count=sum(1 for v in result.violations if v.layer == 4),
                    skipped="Layer 4" in result.skipped_layers_names,
                    skip_reason=result.skip_reason if "Layer 4" in result.skipped_layers_names else ""),
    ]
    return layers
