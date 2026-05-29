"""archguard analyze — full architectural drift analysis."""

from __future__ import annotations
import json

import os
import subprocess
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from archguard.analysis.layers import AnalysisOrchestrator, AnalysisResult
from archguard.analysis.scoring import ArchDebtBand
from archguard.config import EXIT_SUCCESS, EXIT_VIOLATION, EXIT_CONFIG_ERROR, EXIT_ANALYSIS_ERROR
from archguard.utils.errors import format_error, format_warning
from archguard.utils.output import vprint
from archguard.utils.tty import is_tty
from archguard.profiles.defaults import apply_profile

from dataclasses import dataclass, field

@dataclass
class AnalyzeOptions:
    ctx: typer.Context
    repo: Path
    pr_number: int | None = None
    repo_slug: str | None = None
    profile: str | None = None
    changed_files: str | None = None
    skip_explanation: bool = False
    full: bool = False
    json_output: bool = False
    fail_on_warn: bool = False
    dry_run: bool = False
    incremental: bool = False
    no_incremental: bool = False
    no_llm: bool = False
    out_file: Path | None = None
    fail_fast: bool = False
    monorepo: bool = False
    watch: bool = False
    metrics_flag: bool = False
    verbose: bool = False


def attach_explanations(
    result: AnalysisResult,
    explanations: list[str],
) -> AnalysisResult:
    """Return a new AnalysisResult with explanations attached to violations."""
    from dataclasses import replace as dc_replace

    new_violations = []
    for i, v in enumerate(result.violations):
        explanation = explanations[i] if i < len(explanations) else ""
        new_violations.append(dc_replace(v, explanation=explanation))

    return dc_replace(result, violations=new_violations)


analyze_app: typer.Typer = typer.Typer(
    name="analyze",
    help="Analyze the codebase for architectural drift.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

_console: Console = Console()

_BAND_EMOJI: dict[str, str] = {
    "Healthy": "✅ Healthy",
    "Watch": "⚠️ Watch",
    "Warn": "🔶 Warn",
    "Critical": "🚨 Critical",
}


def _resolve_changed_files(
    repo_root: Path,
    changed_files_arg: str | None,
    pr_number: int | None,
    repo_slug: str | None,
) -> list[Path]:
    """Resolve changed files from CLI args, GitHub, or git diff."""
    if changed_files_arg:
        if changed_files_arg.startswith("@"):
            # Read from file
            list_file = Path(changed_files_arg[1:])
            if list_file.is_file():
                raw = list_file.read_text(encoding="utf-8").strip()
                return [repo_root / f.strip() for f in raw.splitlines() if f.strip()]
        # Comma-separated
        return [
            repo_root / f.strip()
            for f in changed_files_arg.split(",")
            if f.strip()
        ]

    if pr_number and repo_slug:
        try:
            from archguard.github.client import GitHubClient

            client = GitHubClient()
            files = client.get_pr_changed_files(repo_slug, pr_number)
            return [repo_root / f for f in files]
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Analysis failed in _resolve_changed_files: {e}", exc_info=True)
            raise

    # Fallback: git diff HEAD~1
    try:
        # First, check if HEAD~1 exists
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD~1"],
            cwd=repo_root,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            # HEAD~1 exists, use normal diff
            diff_cmd = ["git", "diff", "HEAD~1", "--name-only", "--diff-filter=ACMR"]
        else:
            # Initial commit or detached HEAD — diff against empty tree
            # The empty tree hash is a git constant, always valid
            EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
            diff_cmd = ["git", "diff", EMPTY_TREE, "HEAD", "--name-only", "--diff-filter=ACMR"]
            _console.print("[yellow]⚠ Initial commit detected — analyzing all Python files.[/yellow]")

        diff_result = subprocess.run(
            diff_cmd,
            cwd=repo_root,
            capture_output=True,
            text=True
        )

        if diff_result.returncode != 0:
            # Log a warning, don't silently return empty
            import logging
            logging.warning(f"git diff failed: {diff_result.stderr}. Analyzing all Python files.")
            return list(repo_root.rglob("*.py"))
            
        diff_files = [Path(f) for f in diff_result.stdout.strip().splitlines() if f.endswith(".py")]
        return [repo_root / f for f in diff_files if (repo_root / f).exists()]
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Analysis failed in _resolve_changed_files git fallback: {e}", exc_info=True)
        raise

    return []


def _print_rich_report(result: AnalysisResult, repo_root: Path) -> None:
    """Print Rich-formatted analysis report."""
    archdebt = result.archdebt
    band_str = _BAND_EMOJI.get(archdebt.band.value, archdebt.band.value)
    ci_str = "CI PASSED" if not archdebt.should_fail_ci else "CI FAILED"

    _console.print()
    _console.print("[bold]ArchGuard Analysis[/bold]")
    _console.print("─" * 38)
    _console.print(f"Repo:    {repo_root}")
    _console.print(f"Commit:  {result.commit_sha}")
    _console.print(f"Files:   {len(result.changed_files)} changed Python files")
    
    if getattr(result, "partial_analysis", False):
        failures = getattr(result, "parse_failures", [])
        _console.print(f"[bold yellow]⚠ Analysis Partial: {len(failures)} files could not be parsed[/bold yellow]")
        
    _console.print()
    
    from rich.table import Table
    table = Table(title="ArchGuard Analysis Summary", show_header=True)
    table.add_column("Layer", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Score", justify="right")
    table.add_column("Details")
    
    skipped = getattr(result, "skipped_layers_names", [])
    v_counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for v in result.violations:
        v_counts[v.layer] = v_counts.get(v.layer, 0) + 1

    # L1
    s1 = archdebt.layer_scores.layer1_violation
    st1 = "[yellow]SKIPPED[/yellow]" if "boundaries" in skipped else ("[red]FAIL[/red]" if s1 > 0.0 else "[green]PASS[/green]")
    table.add_row("L1 Boundaries", st1, f"{s1:.2f}", f"{v_counts[1]} violations")

    # L2
    s2 = archdebt.layer_scores.layer2_coupling
    st2 = "[yellow]SKIPPED[/yellow]" if "coupling" in skipped else ("[red]FAIL[/red]" if s2 > 0.0 else "[green]PASS[/green]")
    table.add_row("L2 Coupling", st2, f"{s2:.2f}", f"{v_counts[2]} violations")

    # L3
    s3 = archdebt.layer_scores.layer3_drift
    st3 = "[yellow]SKIPPED[/yellow]" if "semantic" in skipped else ("[red]FAIL[/red]" if s3 > 0.0 else "[green]PASS[/green]")
    table.add_row("L3 Drift", st3, f"{s3:.2f}", f"{v_counts[3]} violations")

    # L4
    s4 = archdebt.layer_scores.layer4_duplication
    st4 = "[yellow]SKIPPED[/yellow]" if "duplication" in skipped else ("[red]FAIL[/red]" if s4 > 0.0 else "[green]PASS[/green]")
    table.add_row("L4 Duplication", st4, f"{s4:.2f}", f"{v_counts[4]} violations")

    _console.print(table)
    _console.print(f"\n[bold]ArchDebt Score: {archdebt.composite_score:.2f} — {band_str}[/bold]")
    _console.print(f"Result: {ci_str}\n")

    if result.violations:
        from rich.table import Table
        
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        severity_colors = {
            "critical": "bold red",
            "high": "red",
            "medium": "yellow",
            "low": "dim white",
        }
        
        sorted_violations = sorted(
            result.violations,
            key=lambda v: (severity_order.get(getattr(v, "severity", "low"), 99), v.layer)
        )
        
        _console.print()
        _console.print(f"[bold]Violations ({len(result.violations)}):[/bold]")
        
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Layer", style="dim", width=6)
        table.add_column("Severity", style="bold")
        table.add_column("Module", style="cyan")
        table.add_column("Message")
        
        for v in sorted_violations:
            sev_val = getattr(v, "severity", "low")
            sev_style = severity_colors.get(sev_val, "white")
            sev_str = f"[{sev_style}]{str(sev_val).upper()}[/{sev_style}]"
            table.add_row(
                f"L{v.layer}",
                sev_str,
                v.module,
                f"{v.message} — {v.commit_sha[:7]}"
            )
            
        _console.print(table)
        _console.print("Legend: [bold red]CRITICAL[/bold red] = Layer breach [red]HIGH[/red] = Cycle/Coupling [yellow]MEDIUM[/yellow] = Duplication [dim white]LOW[/dim white] = Cohesion")

    _console.print()
    color = "green" if ci_str == "CI PASSED" else "red"
    _console.print(f"[bold {color}]Result: {ci_str}[/bold {color}]")


def _build_json_report(
    score: float, grade: str, violations: list[dict[str, Any]], metrics: dict[str, float]
) -> dict[str, Any]:
    """Build JSON-serializable dict conforming to the analysis report schema."""
    import datetime

    total_violations = len(violations)
    suppressed_violations = sum(1 for v in violations if v.get("suppressed", False))
    active_violations = total_violations - suppressed_violations

    return {
        "score": score,
        "grade": grade,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "violations": violations,
        "metrics": metrics,
        "summary": {
            "total_violations": total_violations,
            "suppressed_violations": suppressed_violations,
            "active_violations": active_violations,
        },
    }


@analyze_app.callback(invoke_without_command=True)
def analyze_command(
    ctx: typer.Context,
    repo: Path = typer.Option(
        Path("."), "--repo", help="Path to the repository root.",
    ),
    pr: int | None = typer.Option(
        None, "--pr", "--pr-number", help="Pull request number.",
    ),
    repo_slug: str | None = typer.Option(
        None, "--repo-slug", help="Repository slug (e.g. myorg/myrepo).",
    ),
    profile: str = typer.Option(
        None, "--profile", help="Use a preset configuration profile (strict, lenient, ci)."
    ),
    changed_files: str | None = typer.Option(
        None, "--changed-files",
        help="Comma-separated file list or @filename.",
    ),
    skip_explanation: bool = typer.Option(
        False, "--skip-explanation", help="Skip Layer 4 LLM explanation.",
    ),
    full: bool = typer.Option(
        False, "--full", help="Force full corpus rebuild.",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output JSON instead of Rich table.",
    ),
    fail_on_warn: bool = typer.Option(
        False, "--fail-on-warn", help="Exit 1 on Watch band too.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Analyze but don't post PR comment.",
    ),
    incremental: bool = typer.Option(
        False, "--incremental", help="Run incrementally, analyzing only changed files.",
    ),
    no_incremental: bool = typer.Option(
        False, "--no-incremental", help="Force full analysis, ignoring the incremental cache.",
    ),
    no_llm: bool = typer.Option(
        False, "--no-llm", help="Skip LLM API calls entirely.",
    ),
    out_file: Path | None = typer.Option(
        None, "--out-file", help="Write JSON result to this file path (for CI/CD pipelines)"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Show full debug logs.",
    ),
    fail_fast: bool = typer.Option(
        False, "--fail-fast", help="Exit immediately if a layer breaches the fail threshold"
    ),
    metrics_flag: bool = typer.Option(
        False, "--metrics", help="Display performance metrics breakdown"
    ),
    watch: bool = typer.Option(
        False, "--watch", "-w", help="Re-run on file changes",
    ),
    monorepo: bool = typer.Option(
        False, "--monorepo", help="Analyze each sub package separately",
    ),
) -> None:
    """Run architectural drift analysis."""
    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)
        
    try:
        from archguard.utils.validation import validate_repo_path, validate_output_path, PathTraversalError
        from archguard.config import EXIT_CONFIG_ERROR
        repo = validate_repo_path(repo)
        if out_file is not None:
            out_file = validate_output_path(out_file)
    except PathTraversalError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR)

    try:
        if json_output:
            ctx.ensure_object(dict)
            ctx.obj["quiet"] = True
        opts = AnalyzeOptions(
            ctx=ctx,
            repo=repo,
            pr_number=pr,
            repo_slug=repo_slug,
            profile=profile,
            changed_files=changed_files,
            skip_explanation=skip_explanation,
            full=full,
            json_output=json_output,
            fail_on_warn=fail_on_warn,
            dry_run=dry_run,
            incremental=incremental,
            no_incremental=no_incremental,
            no_llm=no_llm,
            out_file=out_file,
            fail_fast=fail_fast,
            monorepo=monorepo,
            watch=watch,
            metrics_flag=metrics_flag,
            verbose=verbose,
        )
        if watch:
            from archguard.cli.watch_cmd import run_watch_mode
            run_watch_mode(opts, Path(repo))
        elif monorepo:
            from archguard.utils.monorepo import detect_subpackages
            packages = detect_subpackages(Path(repo))
            if not packages:
                typer.echo("No sub-packages found. Run without --monorepo.")
                raise typer.Exit(1)
            results = []
            has_error = False
            for pkg in packages:
                pkg_contract = pkg / ".archguard.yml"
                if not pkg_contract.exists():
                    _console.print(f"[yellow]No .archguard.yml in {pkg.name}, skipping[/yellow]")
                    continue
                pkg_opts = AnalyzeOptions(
                    ctx=ctx,
                    repo=pkg,
                    pr_number=pr,
                    repo_slug=repo_slug,
                    profile=profile,
                    changed_files=changed_files,
                    skip_explanation=skip_explanation,
                    full=full,
                    json_output=json_output,
                    fail_on_warn=fail_on_warn,
                    dry_run=dry_run,
                    incremental=incremental,
                    no_incremental=no_incremental,
                    no_llm=no_llm,
                    out_file=out_file,
                    fail_fast=fail_fast,
                    verbose=verbose,
                )
                exit_code, result = _analyze_command_impl(pkg_opts)
                if exit_code not in (0, 1) or result is None:
                    has_error = True
                if result:
                    results.append((pkg.name, result))
            
            _show_monorepo_summary(results)
            if has_error:
                raise typer.Exit(2)
            if any(r.archdebt.should_fail_ci for _, r in results):
                raise typer.Exit(1)
        else:
            exit_code, result = _analyze_command_impl(opts)
            if exit_code != 0:
                raise typer.Exit(exit_code)
    except typer.Exit:
        raise
    except Exception as e:
        from rich.console import Console
        console = Console(stderr=True)
        console.print(f"[bold red]ArchGuard Error:[/bold red] {e}")
        if verbose:
            console.print_exception()
        else:
            console.print("[dim]Run with --verbose for full traceback[/dim]")
        raise typer.Exit(1)

def _analyze_command_impl(opts: AnalyzeOptions) -> tuple[int, AnalysisResult | None]:
    
    SKIP_LLM = os.getenv("ARCHGUARD_SKIP_LLM", "").lower() in ("1", "true", "yes")
    if opts.no_llm or SKIP_LLM:
        opts.skip_explanation = True
    repo_root = opts.repo.resolve()

    # Auto-detect GitHub Actions env vars
    opts.repo_slug = opts.repo_slug or os.environ.get("GITHUB_REPOSITORY")
    if opts.pr_number is None:
        from archguard.github.client import _get_pr_number
        opts.pr_number = _get_pr_number()

    # Load contract
    try:
        orchestrator = AnalysisOrchestrator(repo_root)
    except Exception as e:
        _console.print(format_error(f"Failed to load contract: {e}"))
        return EXIT_CONFIG_ERROR, None

    # Apply opts.profile
    profile_to_use = opts.profile or orchestrator.contract.get("profile")
    if profile_to_use:
        orchestrator.contract = apply_profile(orchestrator.contract, profile_to_use)
        vprint(f"Applied configuration opts.profile: [bold cyan]{profile_to_use}[/bold cyan]", opts.ctx)

    # Resolve changed files
    all_changed = _resolve_changed_files(
        repo_root, opts.changed_files, opts.pr_number, opts.repo_slug,
    )
    vprint(
        f"[bold blue]Analyzing {len(all_changed)} changed file(s)[/bold blue]", opts.ctx
    )
    py_changed = [f for f in all_changed if str(f).endswith(".py")]

    from archguard.cache.incremental import get_changed_files, save_cache, load_cache, FileRecord, compute_hash
    from archguard.audit.logger import AuditLogger
    
    unchanged: list[Path] = []
    if opts.incremental and not opts.no_incremental:
        py_changed, unchanged = get_changed_files(py_changed, repo_root)

    if not py_changed and not unchanged:
        vprint("No Python files changed. Skipping analysis.", opts.ctx)
        return EXIT_SUCCESS, None

    # Get commit SHA
    commit_sha = AnalysisOrchestrator.get_commit_sha(repo_root)

    from archguard.utils.errors import ArchGuardError
    import sys

    # Run analysis
    
    quiet = opts.ctx.obj.get("quiet", False)
    use_rich = is_tty() and not quiet

    try:
        vprint(f"Analyzing {len(py_changed)} changed files...", opts.ctx, level="debug")
        result = orchestrator.run(
            py_changed, commit_sha, skip_explanation=opts.skip_explanation,
            progress_callback=None, fail_fast=opts.fail_fast,
            quiet=opts.json_output
        )
        
        if opts.verbose or opts.metrics_flag:
            from rich.table import Table
            table = Table(title="Performance Metrics")
            table.add_column("Layer", style="cyan")
            table.add_column("Duration", justify="right", style="magenta")
            for layer, duration in result.metrics.get("layer_durations", {}).items():
                table.add_row(layer, f"{duration:.3f}s")
            _console.print(table)

        vprint("Analysis core completed.", opts.ctx, level="debug")
        
        # Merge opts.incremental results
        if opts.incremental and not opts.no_incremental and unchanged:
            last_run = AuditLogger(repo_root / "audit.jsonl").read_last_run()
            if last_run:
                unchanged_rel = {str(f.relative_to(repo_root)).replace("\\", "/") for f in unchanged}
                from archguard.analysis.layers import ViolationDetail
                from archguard.analysis.scoring import compute_archdebt, LayerScores
                
                # Extract previous violations
                for v in last_run.get("violations", []):
                    if v.get("file") in unchanged_rel:
                        result.violations.append(ViolationDetail(
                            layer=v.get("layer", 0),
                            module=v.get("module", ""),
                            message=v.get("message", ""),
                            commit_sha=commit_sha[:7],
                            file_path=v.get("file", ""),
                            explanation=v.get("explanation", "")
                        ))
                
                # Restore previous metrics to avoid skewed scores
                metrics = last_run.get("metrics", {})
                if metrics:
                    scores = LayerScores(
                        layer1_violation=metrics.get("layer_score", 0) / 100.0,
                        layer2_coupling=metrics.get("coupling_score", 0) / 100.0,
                        layer3_drift=metrics.get("semantic_score", 0) / 100.0,
                        layer4_duplication=metrics.get("duplication_score", 0) / 100.0,
                    )
                    
                    weights_cfg = orchestrator.contract.get("weights", {})
                    weights = (
                        float(weights_cfg.get("layer1", 0.25)),
                        float(weights_cfg.get("layer2", 0.25)),
                        float(weights_cfg.get("layer3", 0.25)),
                        float(weights_cfg.get("layer4", 0.25)),
                    )
                    
                    result.layer_scores = scores
                    result.archdebt = compute_archdebt(
                        scores,
                        weights=weights,
                        fail_threshold=float(orchestrator.contract.get("fail_threshold", 0.75)),
                        warn_threshold=float(orchestrator.contract.get("warn_threshold", 0.50)),
                    )
                
                # Combine changed files
                result.changed_files.extend(list(unchanged_rel))
        
        # Save opts.incremental cache on success
        if opts.incremental and not opts.no_incremental:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            cache_records = load_cache(repo_root)
            for f in py_changed + unchanged:
                rel = str(f.relative_to(repo_root)).replace("\\", "/")
                cache_records[rel] = FileRecord(
                    path=rel,
                    sha256=compute_hash(f),
                    last_analyzed=now
                )
            save_cache(repo_root, cache_records)
            
    except ArchGuardError as e:
        _console.print(format_error(e.message))
        return EXIT_ANALYSIS_ERROR, None
    except RuntimeError as exc:
        if "ML dependencies" in str(exc):
            _console.print(
                "\n[bold red]Missing Dependencies[/bold red]\n"
                "Layer 3 requires ML libraries. Install with:\n"
                "  pip install archguard\\[ml]\n"
                "Or skip this layer by adding to .archguard.yml:\n"
                "  skip_layers: [semantic]"
            )
            return EXIT_CONFIG_ERROR, None
        else:
            _console.print(format_error(f"Analysis failed: {exc}"))
            return EXIT_ANALYSIS_ERROR, None
    except Exception as exc:
        _console.print(format_error(f"Analysis failed: {exc}"))
        return EXIT_ANALYSIS_ERROR, None

    # LLM explanation (unless skipped)
    if (
        not opts.skip_explanation
        and result.archdebt.should_fail_ci
        and result.violations
    ):
        vprint(f"Requesting LLM explanations for {len(result.violations)} violations...", opts.ctx, level="debug")
        
        progress = None
        task = None
        if use_rich:
            from rich.progress import Progress, SpinnerColumn, TextColumn
            progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=_console,
                transient=True
            )
            progress.start()
            task = progress.add_task("[yellow]Generating LLM Explanations...[/yellow]", total=None)

        try:
            from archguard.llm.cloud import CloudLLMExplainer
            import asyncio

            explainer = CloudLLMExplainer()
            
            raw_explanations = asyncio.run(explainer.explain_violations_concurrent(
                result.violations, orchestrator.contract, result.changed_files
            ))
            
            explanations = []
            for exp in raw_explanations:
                if isinstance(exp, Exception):
                    import logging
                    logging.getLogger(__name__).warning(f"Concurrent explanation failed: {exp}")
                    explanations.append("[Explanation unavailable]")
                else:
                    explanations.append(exp)
                    
            vprint("LLM explanations received and attached.", opts.ctx, level="debug")
            result = attach_explanations(result, explanations)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Non-critical failure in LLM explanation: {e}")
            if not quiet:
                _console.print(format_warning(
                    "LLM explanation failed. Continuing without explanations."
                ))
        finally:
            if progress:
                if task is not None:
                    progress.update(task, description="[green]✓ Explanations Generated[/green]")
                progress.stop()

    # Always build v_list_out for output and audit
    v_list_out = []
    for v in result.violations:
        v_list_out.append({
            "type": "layer",
            "layer": getattr(v, "layer", 0),
            "file": str(getattr(v, "file_path", getattr(v, "module", ""))),
            "message": getattr(v, "message", ""),
            "severity": str(getattr(v, "severity", "low")),
            "suppressed": getattr(v, "suppressed", False),
            "explanation": getattr(v, "explanation", "")
        })

    if opts.out_file is not None:
        band_val = str(result.archdebt.band.name).upper()
        if band_val in ("HEALTHY", "WATCH"):
            out_band = "PASS"
        elif band_val == "WARN":
            out_band = "WARN"
        else:
            out_band = "FAIL"

        result_dict = {
            "score": result.archdebt.composite_score * 100,
            "band": out_band,
            "violations": v_list_out,
            "layer_results": {
                "layer1_violation": float(result.archdebt.layer_scores.layer1_violation) * 100,
                "layer2_coupling": float(result.archdebt.layer_scores.layer2_coupling) * 100,
                "layer3_drift": float(result.archdebt.layer_scores.layer3_drift) * 100,
                "layer4_duplication": float(result.archdebt.layer_scores.layer4_duplication) * 100,
            },
            "fail_fast_triggered": getattr(result, "fail_fast_triggered", False)
        }
        if getattr(result, "fail_fast_triggered", False):
            result_dict["skipped_layers"] = [{"status": "skipped", "reason": "fail-fast", "layer": layer} for layer in getattr(result, "skipped_layers_names", [])]
        opts.out_file.parent.mkdir(parents=True, exist_ok=True)
        opts.out_file.write_text(json.dumps(result_dict, indent=2, default=str))

    if opts.json_output:
        score = result.archdebt.composite_score * 100
        grade = str(result.archdebt.band.value)
        
        v_list = []
        for v in result.violations:
            v_list.append({
                "type": "layer",
                "file": str(getattr(v, "file_path", getattr(v, "module", ""))),
                "message": getattr(v, "message", ""),
                "severity": str(getattr(v, "severity", "low")),
                "suppressed": getattr(v, "suppressed", False),
                "explanation": getattr(v, "explanation", "")
            })
            
        metrics = {
            "layer_score": float(result.archdebt.layer_scores.layer1_violation) * 100,
            "coupling_score": float(result.archdebt.layer_scores.layer2_coupling) * 100,
            "duplication_score": float(result.archdebt.layer_scores.layer4_duplication) * 100,
            "semantic_score": float(result.archdebt.layer_scores.layer3_drift) * 100,
        }
        
        report = _build_json_report(score, grade, v_list, metrics)
        report["fail_fast_triggered"] = getattr(result, "fail_fast_triggered", False)
        if getattr(result, "fail_fast_triggered", False):
            report["skipped_layers"] = [{"status": "skipped", "reason": "fail-fast", "layer": layer} for layer in getattr(result, "skipped_layers_names", [])]
        typer.echo(json.dumps(report, indent=2))
    else:
        if opts.ctx.obj.get("quiet"):
            ci_str = "PASSED" if not result.archdebt.should_fail_ci else "FAILED"
            _console.print(f"ArchDebt Score: {result.archdebt.composite_score:.2f} | CI: {ci_str}")
        else:
            _print_rich_report(result, repo_root)

    # Log analysis completion to audit log
    try:
        from archguard.audit.logger import AuditLogger
        from archguard.config import AUDIT_EVENT_ANALYSIS
        audit = AuditLogger(log_path=repo_root / ".archguard-cache" / "audit.jsonl")
        
        band_val = str(result.archdebt.band.name).upper()
        if band_val in ("HEALTHY", "WATCH"):
            audit_band = "PASS"
        elif band_val == "WARN":
            audit_band = "WARN"
        else:
            audit_band = "FAIL"
            
        audit.log(
            AUDIT_EVENT_ANALYSIS,
            score=result.archdebt.composite_score * 100,
            band=audit_band,
            pr_number=opts.pr_number,
            violations=v_list_out,
            metrics=result.metrics,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to log analysis_complete: {e}")

    # Post PR comment (if applicable)
    if opts.repo_slug and not opts.dry_run:
        try:
            token = os.environ.get("GITHUB_TOKEN")
            head_sha = os.environ.get("GITHUB_SHA") or commit_sha
            
            if token and head_sha:
                from archguard.github.checks import ChecksAPIClient
                from archguard.github.annotation_builder import violations_to_annotations
                
                checks_client = ChecksAPIClient(token=token, repo_full_name=opts.repo_slug)
                annotations = violations_to_annotations(v_list_out)
                
                fail_threshold = float(orchestrator.contract.get("fail_threshold", 0.75))
                from typing import Literal
                conclusion: Literal['success', 'failure'] = "failure" if result.archdebt.composite_score > fail_threshold else "success"
                
                checks_client.create_check_run(
                    name="ArchGuard",
                    head_sha=head_sha,
                    status="completed",
                    conclusion=conclusion,
                    title=f"ArchDebt: {result.archdebt.composite_score:.2f} ({result.archdebt.band.value})",
                    summary="ArchGuard analysis complete.",
                    annotations=annotations,
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Non-critical failure in Checks API: {e}")

        try:
            from archguard.github.client import post_comment
            from archguard.github.comments import PRCommentManager

            token = os.environ.get("GITHUB_TOKEN")
            client = None
            if token:
                from archguard.github.client import GitHubClient
                try:
                    client = GitHubClient(token=token)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(f"Non-critical failure in GitHubClient init: {e}")
            manager = PRCommentManager(client)  # type: ignore
            body = manager.format_report(result)
            post_comment(opts.repo_slug, body, pr_number=opts.pr_number, token=token)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Non-critical failure in PR comment posting: {e}")
            _console.print("[yellow]Warning: Failed to post PR comment.[/yellow]")

    # Determine exit code
    has_critical_parse_failures = any(f.is_critical for f in getattr(result, "parse_failures", []))
    if has_critical_parse_failures:
        if not quiet:
            _console.print("[bold red]Critical parse failures detected. Analysis is invalid.[/bold red]")
        return 3, result

    should_fail = result.archdebt.should_fail_ci
    if opts.fail_on_warn and result.archdebt.band in (
        ArchDebtBand.WATCH,

        ArchDebtBand.WARN,
    ):
        should_fail = True

    # Slack/Webhook Alerting
    slack_webhook = os.getenv("ARCHGUARD_SLACK_WEBHOOK")
    if slack_webhook:
        try:
            import asyncio
            from archguard.alerting.trend_detector import detect_trends
            from archguard.alerting.webhooks import send_slack_alert
            
            from archguard.audit.logger import AuditLogger
            audit_logger = AuditLogger(log_path=repo_root / ".archguard-cache" / "audit.jsonl")
            runs = audit_logger.read_last_n_runs(n=10)
            alerts = detect_trends(runs, window=10)
            if alerts:
                asyncio.run(send_slack_alert(slack_webhook, alerts))
        except Exception as e:
            _console.print(format_warning(f"Failed to send Slack alert: {e}"))

    if should_fail:
        return EXIT_VIOLATION, result

    return EXIT_SUCCESS, result

def _show_monorepo_summary(results: list[tuple[str, AnalysisResult]]) -> None:
    from rich.table import Table
    table = Table(title="Monorepo Analysis Summary")
    table.add_column("Package")
    table.add_column("ArchDebt")
    table.add_column("Band")
    table.add_column("Violations")
    
    for name, result in results:
        table.add_row(
            name, 
            f"{result.archdebt.composite_score:.3f}", 
            result.archdebt.band.value,
            str(len(result.violations))
        )
    _console.print(table)
    
    if results:
        avg_score = sum(r.archdebt.composite_score for _, r in results) / len(results)
        _console.print(f"\nMonorepo ArchDebt: {avg_score:.3f}")
