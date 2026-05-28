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
from archguard.config import EXIT_OK, EXIT_VIOLATION
from archguard.utils.errors import ConfigError, format_error, format_warning
from archguard.utils.output import vprint
from archguard.utils.tty import is_tty
from archguard.profiles.defaults import apply_profile

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
        except Exception:  # noqa: BLE001
            pass

    # Fallback: git diff
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD~1", "--name-only"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=30,
        )
        if result.returncode == 0:
            return [
                repo_root / f.strip()
                for f in result.stdout.strip().splitlines()
                if f.strip()
            ]
    except Exception:  # noqa: BLE001
        pass

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
    _console.print(f"ArchDebt Score:  {archdebt.composite_score:.2f}   {band_str}")
    _console.print()
    _console.print("[bold]Layer Scores:[/bold]")
    _console.print(f"  L1 Import:      {archdebt.layer_scores.layer1_violation:.2f}")
    _console.print(f"  L2 Coupling:    {archdebt.layer_scores.layer2_coupling:.2f}")
    _console.print(f"  L3 Drift:       {archdebt.layer_scores.layer3_drift:.2f}")
    _console.print(f"  L4 Duplication: {archdebt.layer_scores.layer4_duplication:.2f}")

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
) -> None:
    """Run architectural drift analysis."""
    
    if no_llm:
        skip_explanation = True
    repo_root = repo.resolve()

    # Auto-detect GitHub Actions env vars
    repo_slug = repo_slug or os.environ.get("GITHUB_REPOSITORY")
    if pr is None:
        from archguard.github.client import _get_pr_number
        pr = _get_pr_number()

    # Load contract
    try:
        orchestrator = AnalysisOrchestrator(repo_root)
    except Exception as e:
        _console.print(format_error("Failed to load contract", e))
        raise typer.Exit(EXIT_VIOLATION) from e

    # Apply profile
    profile_to_use = profile or orchestrator.contract.get("profile")
    if profile_to_use:
        orchestrator.contract = apply_profile(orchestrator.contract, profile_to_use)
        vprint(f"Applied configuration profile: [bold cyan]{profile_to_use}[/bold cyan]", ctx)

    # Resolve changed files
    all_changed = _resolve_changed_files(
        repo_root, changed_files, pr, repo_slug,
    )
    vprint(
        f"[bold blue]Analyzing {len(all_changed)} changed file(s)[/bold blue]", ctx
    )
    py_changed = [f for f in all_changed if str(f).endswith(".py")]

    from archguard.cache.incremental import get_changed_files, save_cache, load_cache, FileRecord, compute_hash
    from archguard.audit.logger import AuditLogger
    
    unchanged = []
    if incremental and not no_incremental:
        py_changed, unchanged = get_changed_files(py_changed, repo_root)

    if not py_changed and not unchanged:
        vprint("No Python files changed. Skipping analysis.", ctx)
        raise typer.Exit(EXIT_OK)

    # Get commit SHA
    commit_sha = AnalysisOrchestrator.get_commit_sha(repo_root)

    from archguard.utils.errors import ArchGuardError
    import sys

    # Run analysis
    from contextlib import ExitStack
    
    quiet = ctx.obj.get("quiet", False)
    use_rich = is_tty() and not quiet

    with ExitStack() as stack:
        progress_ctx = None
        task = None

        if use_rich:
            from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
            progress_ctx = stack.enter_context(
                Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                    TimeElapsedColumn(),
                    console=_console
                )
            )
            total_steps = 4 + (0 if skip_explanation else 1)
            task = progress_ctx.add_task("Analyzing...", total=total_steps)

        def progress_cb(desc: str) -> None:
            if progress_ctx and task is not None:
                progress_ctx.update(task, description=f"[cyan]{desc}[/cyan]")
                progress_ctx.advance(task)

        try:
            vprint(f"Analyzing {len(py_changed)} changed files...", ctx, level="debug")
            result = orchestrator.run(
                py_changed, commit_sha, skip_explanation=skip_explanation,
                progress_callback=progress_cb
            )
            vprint("Analysis core completed.", ctx, level="debug")
            
            # Merge incremental results
            if incremental and not no_incremental and unchanged:
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
            
            # Save incremental cache on success
            if incremental and not no_incremental:
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
            sys.exit(1)
        except Exception as exc:
            _console.print(format_error(f"Analysis failed: {exc}"))
            raise typer.Exit(2) from exc

        # LLM explanation (unless skipped)
        if (
            not skip_explanation
            and result.archdebt.should_fail_ci
            and result.violations
        ):
            if progress_ctx and task is not None:
                progress_ctx.update(task, description="[yellow]LLM Explanations[/yellow]")
            vprint(f"Requesting LLM explanations for {len(result.violations)} violations...", ctx, level="debug")
            try:
                from archguard.llm.cloud import CloudLLMExplainer

                explainer = CloudLLMExplainer()
                llm_result = explainer.explain(result, orchestrator.contract)
                if llm_result.unavailable:
                    if not quiet:
                        _console.print(format_warning(
                            "LLM explanation unavailable. "
                            "Violation report will be posted without explanations."
                        ))
                else:
                    vprint("LLM explanations received and attached.", ctx, level="debug")
                    result = attach_explanations(result, llm_result.explanations)
            except Exception:  # noqa: BLE001
                if not quiet:
                    _console.print(format_warning(
                        "LLM explanation failed. Continuing without explanations."
                    ))
            if progress_ctx and task is not None:
                progress_ctx.advance(task)
        elif not skip_explanation:
            if progress_ctx and task is not None:
                progress_ctx.advance(task)


    # Output
    if json_output:
        import click
        
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
        click.echo(json.dumps(report, indent=2))
    else:
        if ctx.obj.get("quiet"):
            ci_str = "PASSED" if not result.archdebt.should_fail_ci else "FAILED"
            _console.print(f"ArchDebt Score: {result.archdebt.composite_score:.2f} | CI: {ci_str}")
        else:
            _print_rich_report(result, repo_root)

    # Post PR comment (if applicable)
    if repo_slug and not dry_run:
        try:
            from archguard.github.client import post_comment
            from archguard.github.comments import PRCommentManager

            token = os.environ.get("GITHUB_TOKEN")
            client = None
            if token:
                from archguard.github.client import GitHubClient
                try:
                    client = GitHubClient(token=token)
                except Exception:
                    pass
            manager = PRCommentManager(client)  # type: ignore
            body = manager.format_report(result)
            post_comment(repo_slug, body, pr_number=pr, token=token)
        except Exception:  # noqa: BLE001
            _console.print("[yellow]Warning: Failed to post PR comment.[/yellow]")

    # Determine exit code
    should_fail = result.archdebt.should_fail_ci
    if fail_on_warn and result.archdebt.band in (
        ArchDebtBand.WATCH,
        ArchDebtBand.WARN,
    ):
        should_fail = True

    if should_fail:
        raise typer.Exit(EXIT_VIOLATION)
