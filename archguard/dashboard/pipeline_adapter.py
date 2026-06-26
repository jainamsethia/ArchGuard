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
from typing import Awaitable, Callable, Any

logger = logging.getLogger(__name__)

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
    modules_analyzed: int = 0
    duration_seconds: float = 0.0
    contract_auto_generated: bool = False
    skipped: bool = False
    skip_reason: str = ""
    error: str | None = None

# --------------------------------------------------------------------------
# Public interface
# --------------------------------------------------------------------------

ProgressCallback = Callable[[str], Awaitable[None]]

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

    # ── Step 1: Auto-generate contract if absent ─────────────────────────
    archguard_yml = repo_path / ".archguard.yml"
    if not archguard_yml.exists():
        await _emit("No .archguard.yml found — generating contract from directory structure…")
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, _generate_contract_sync, repo_path
            )
            contract_auto_generated = True
            await _emit("Contract auto-generated from directory structure.")
        except Exception as exc:
            logger.warning("[job %s] Contract auto-generation failed: %s", job_id, exc)
            await _emit(f"Contract generation warning: {exc}. Attempting analysis anyway.")

    # ── Step 2: Collect all Python files (fresh clone = all files changed) ──
    py_files = list(repo_path.rglob("*.py"))
    # Exclude hidden dirs and __pycache__
    py_files = [
        f for f in py_files
        if not any(part.startswith(".") or part == "__pycache__" for part in f.relative_to(repo_path).parts)
    ]

    if not py_files:
        elapsed = round(time.monotonic() - start, 1)
        return AnalysisJobResult(
            job_id=job_id, repo_url=repo_url,
            health_score=0.0, health_grade="F",
            composite_score=1.0, skipped=True,
            skip_reason="No Python files found in repository",
            duration_seconds=elapsed,
            contract_auto_generated=contract_auto_generated,
        )

    await _emit(f"Found {len(py_files)} Python files. Starting 4-layer analysis…")

    # ── Step 3: Run analysis in thread pool ─────────────────────────────
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            _run_analysis_sync,
            repo_path,
            py_files,
            skip_explanation,
        )
    except Exception as exc:
        elapsed = round(time.monotonic() - start, 1)
        logger.exception("[job %s] Analysis failed", job_id)
        return AnalysisJobResult(
            job_id=job_id, repo_url=repo_url,
            health_score=0.0, health_grade="F",
            composite_score=1.0,
            duration_seconds=elapsed,
            contract_auto_generated=contract_auto_generated,
            error=str(exc),
        )

    elapsed = round(time.monotonic() - start, 1)
    layer_results = _extract_layer_results(result)
    total_violations = len(result.violations)

    try:
        from archguard.audit.logger import AuditLogger
        from archguard.config import AUDIT_LOG_FILENAME
        
        audit_dir = Path.cwd() / ".archguard-cache"
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_path = audit_dir / AUDIT_LOG_FILENAME
        
        audit = AuditLogger(log_path=audit_path)
        audit.log_run(
            repo_url=repo_url,
            job_id=job_id,
            health_score=result.archdebt.health_score,
            health_grade=result.archdebt.health_grade,
            composite_score=result.archdebt.composite_score,
            total_violations=total_violations,
            duration_seconds=elapsed,
        )
    except Exception as e:
        logger.warning("Failed to write audit log: %s", e)

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
        modules_analyzed=result.modules_analyzed,
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
        _console=Console(quiet=True),
    )

def _run_analysis_sync(
    repo_path: Path,
    py_files: list[Path],
    skip_explanation: bool,
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

    # ── Log the result for the dashboard context ──
    try:
        from archguard.audit.logger import AuditLogger
        from archguard.config import AUDIT_EVENT_ANALYSIS
        
        audit = AuditLogger(log_path=repo_path / ".archguard-cache" / "audit.jsonl")
        band_val = str(result.archdebt.band.name).upper()
        audit_band = (
            "PASS"
            if band_val in ("HEALTHY", "WATCH")
            else ("WARN" if band_val == "WARN" else "FAIL")
        )
        
        v_list_out = []
        for v in result.violations:
            v_list_out.append(
                {
                    "type": "layer",
                    "layer": getattr(v, "layer", 0),
                    "file": str(getattr(v, "file_path", getattr(v, "module", ""))),
                    "message": getattr(v, "message", ""),
                    "severity": str(getattr(v, "severity", "low")),
                    "suppressed": getattr(v, "suppressed", False),
                    "explanation": getattr(v, "explanation", ""),
                }
            )
            
        audit.log(
            AUDIT_EVENT_ANALYSIS,
            score=result.archdebt.health_score,
            band=audit_band,
            pr_number=None,
            violations=v_list_out,
            metrics=result.metrics,
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
