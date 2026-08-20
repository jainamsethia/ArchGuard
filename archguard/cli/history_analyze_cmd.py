"""archguard history-analyze - analyze historical commits and report evolution."""

from __future__ import annotations

import json
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)

history_analyze_app: typer.Typer = typer.Typer(
    name="history-analyze",
    help="Analyze historical commits to track architecture evolution.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

_console = Console()

_TREND_ARROWS = {
    "improving": "[green]↑ improving[/green]",
    "stable": "[dim]− stable[/dim]",
    "declining": "[red]↓ declining[/red]",
}


def _get_commit_shas(repo: Path, max_commits: int) -> list[str]:
    """Retrieve the last N commit SHAs from the repo, oldest first."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "log", "--format=%H", f"-{max_commits}"],
            capture_output=True,
            text=True,
            check=True,
        )
        shas = [s.strip() for s in result.stdout.strip().splitlines() if s.strip()]
        shas.reverse()  # oldest first
        return shas
    except subprocess.CalledProcessError:
        return []


def _analyze_commit(repo: Path, commit_sha: str) -> dict[str, Any] | None:
    """Run analysis on a single commit inside a worktree, return audit-shaped dict or None."""
    from archguard.evolution.worktree import GitWorktreeManager

    manager = GitWorktreeManager(repo)
    try:
        with manager.checkout(commit_sha) as wt_path:
            return _run_analysis_in_worktree(wt_path, commit_sha)
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            f"Failed to analyze commit {commit_sha[:7]}: {e}"
        )
        return None


def _run_analysis_in_worktree(wt_path: Path, commit_sha: str) -> dict[str, Any] | None:
    """Run the analysis pipeline inside a worktree directory."""
    import datetime

    try:
        from archguard.analysis.layers import AnalysisOrchestrator

        orchestrator = AnalysisOrchestrator(wt_path)
        result = orchestrator.run([wt_path], commit_sha, quiet=True)

        # Build audit-shaped dict for EvolutionTracker consumption
        violations = []
        for v in result.violations:
            violations.append(
                {
                    "layer": getattr(v, "layer", 0),
                    "message": getattr(v, "message", ""),
                }
            )

        snapshot: dict[str, Any] = {
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "score": result.archdebt.health_score,
            "violations": violations,
            "metrics": getattr(result, "metrics", {}) or {},
            "commit_sha": commit_sha,
        }
        return snapshot
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            f"Analysis failed in worktree for {commit_sha[:7]}: {e}"
        )
        return None


def _calc_debt_velocity(snapshots: list[dict[str, Any]]) -> float | None:
    """Calculate debt velocity as the change in debt score per commit.

    debt_score = 1.0 - (health_score / 100.0)
    velocity = (last_debt - first_debt) / (num_snapshots - 1)
    Negative velocity means debt is decreasing (improving).
    """
    if len(snapshots) < 2:
        return None
    first_health = float(snapshots[0].get("score", 0.0))
    last_health = float(snapshots[-1].get("score", 0.0))
    first_debt = 1.0 - (first_health / 100.0)
    last_debt = 1.0 - (last_health / 100.0)
    return (last_debt - first_debt) / (len(snapshots) - 1)


def _sparkline(values: list[float]) -> str:
    """Generate a sparkline string from a list of values."""
    bars = "▁▂▃▄▅▆▇█"
    if not values:
        return ""
    min_v, max_v = min(values), max(values)
    rng = max_v - min_v or 1
    return "".join(bars[int((v - min_v) / rng * 7)] for v in values)


def _print_rich_report(
    snapshots: list[dict[str, Any]],
    report: Any,
    debt_velocity: float | None,
) -> None:
    """Print a Rich-formatted evolution report to the console."""
    _console.print()
    _console.print("[bold]Architecture Evolution Report[/bold]")
    _console.print("-" * 42)

    # Summary table
    table = Table(title="Evolution Summary", show_header=True)
    table.add_column("Metric", style="bold")
    table.add_column("Current", justify="right")
    table.add_column("Previous", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("Trend", justify="center")

    for trend in [report.health_trend, report.violation_trend, report.debt_trend]:
        prev_str = (
            f"{trend.previous_value:.2f}" if trend.previous_value is not None else "-"
        )
        delta_str = f"{trend.delta:+.4f}" if trend.delta is not None else "-"
        arrow = _TREND_ARROWS.get(
            trend.classification.value, trend.classification.value
        )
        table.add_row(
            trend.name, f"{trend.current_value:.2f}", prev_str, delta_str, arrow
        )

    if report.fitness_trend is not None:
        ft = report.fitness_trend
        prev_str = f"{ft.previous_value:.2f}" if ft.previous_value is not None else "-"
        delta_str = f"{ft.delta:+.4f}" if ft.delta is not None else "-"
        arrow = _TREND_ARROWS.get(ft.classification.value, ft.classification.value)
        table.add_row(ft.name, f"{ft.current_value:.2f}", prev_str, delta_str, arrow)

    _console.print(table)

    # Debt velocity
    if debt_velocity is not None:
        vel_sign = "+" if debt_velocity >= 0 else ""
        vel_color = (
            "red" if debt_velocity > 0 else ("green" if debt_velocity < 0 else "dim")
        )
        _console.print(
            f"\n[bold]Debt Velocity:[/bold] [{vel_color}]{vel_sign}{debt_velocity:.4f} per commit[/{vel_color}]"
        )
    else:
        _console.print("\n[bold]Debt Velocity:[/bold] [dim]Insufficient data[/dim]")

    # Score range
    scores = [float(s.get("score", 0.0)) for s in snapshots]
    if scores:
        min_s, max_s = min(scores), max(scores)
        _console.print(f"[bold]Score Range:[/bold] {min_s:.1f} - {max_s:.1f}")
        _console.print(f"[bold]Sparkline:[/bold]  {_sparkline(scores)}")

    # Commits analyzed
    _console.print(f"\n[dim]Commits analyzed: {len(snapshots)}[/dim]")
    _console.print()


def _build_json_output(
    snapshots: list[dict[str, Any]],
    report: Any,
    debt_velocity: float | None,
) -> dict[str, Any]:
    """Build a JSON-serializable dict for the evolution report."""
    scores = [float(s.get("score", 0.0)) for s in snapshots]

    def _trend_to_dict(trend: Any) -> dict[str, Any]:
        return {
            "name": trend.name,
            "current_value": trend.current_value,
            "previous_value": trend.previous_value,
            "delta": trend.delta,
            "classification": trend.classification.value,
        }

    result: dict[str, Any] = {
        "commits_analyzed": len(snapshots),
        "score_range": {
            "min": min(scores) if scores else None,
            "max": max(scores) if scores else None,
        },
        "debt_velocity": debt_velocity,
        "trends": {
            "health": _trend_to_dict(report.health_trend),
            "violations": _trend_to_dict(report.violation_trend),
            "debt": _trend_to_dict(report.debt_trend),
        },
        "snapshots": [
            {
                "commit_sha": s.get("commit_sha", ""),
                "score": float(s.get("score", 0.0)),
                "violation_count": len(s.get("violations", [])),
            }
            for s in snapshots
        ],
    }
    if report.fitness_trend is not None:
        result["trends"]["fitness"] = _trend_to_dict(report.fitness_trend)
    return result


@history_analyze_app.callback(invoke_without_command=True)
def history_analyze(
    ctx: typer.Context,
    repo: Path = typer.Option(
        Path("."),
        "--repo",
        help="Path to the repository root.",
    ),
    max_commits: int = typer.Option(
        20,
        "--max-commits",
        "-n",
        help="Maximum number of historical commits to analyze.",
    ),
    workers: int = typer.Option(
        1,
        "--workers",
        "-w",
        help="Number of parallel workers for historical analysis.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output JSON instead of Rich table.",
    ),
) -> None:
    """Analyze historical commits and report architecture evolution trends."""
    try:
        from archguard.utils.validation import validate_repo_path

        repo = validate_repo_path(repo)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(2)

    # Step 1: Get commit history
    shas = _get_commit_shas(repo, max_commits)
    if not shas:
        if json_output:
            typer.echo(
                json.dumps({"error": "No commits found.", "commits_analyzed": 0})
            )
        else:
            _console.print("[yellow]No commits found in repository.[/yellow]")
        raise typer.Exit(0)

    if not json_output:
        _console.print(
            f"[bold]Analyzing {len(shas)} commits with {workers} worker(s)...[/bold]"
        )

    # Step 2: Analyze each commit (sequentially or in parallel)
    snapshots: list[dict[str, Any]] = []
    failed_count = 0

    if workers <= 1:
        for sha in shas:
            if not json_output:
                _console.print(f"  [dim]Analyzing {sha[:7]}...[/dim]")
            result = _analyze_commit(repo, sha)
            if result is not None:
                snapshots.append(result)
            else:
                failed_count += 1
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_sha = {
                executor.submit(_analyze_commit, repo, sha): sha for sha in shas
            }
            for future in as_completed(future_to_sha):
                sha = future_to_sha[future]
                try:
                    result = future.result()
                    if result is not None:
                        snapshots.append(result)
                    else:
                        failed_count += 1
                except Exception as exc:
                    logger.exception("Commit analysis failed: %s", exc)
                    failed_count += 1

        # Re-sort by commit order (parallel execution may scramble order)
        sha_order = {sha: i for i, sha in enumerate(shas)}
        snapshots.sort(key=lambda s: sha_order.get(s.get("commit_sha", ""), 0))

    if not snapshots:
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "error": "All commit analyses failed.",
                        "commits_analyzed": 0,
                        "failed_count": failed_count,
                    }
                )
            )
        else:
            _console.print("[red]All commit analyses failed.[/red]")
        raise typer.Exit(1)

    # Step 3: Feed to EvolutionTracker
    from archguard.evolution.tracker import EvolutionTracker

    tracker = EvolutionTracker(snapshots)
    report = tracker.generate_report()

    # Step 4: Calculate debt velocity
    debt_velocity = _calc_debt_velocity(snapshots)

    # Step 5: Output
    if json_output:
        output = _build_json_output(snapshots, report, debt_velocity)
        output["failed_count"] = failed_count
        typer.echo(json.dumps(output, indent=2))
    else:
        if failed_count > 0:
            _console.print(
                f"[yellow][!] {failed_count} commit(s) failed analysis and were skipped.[/yellow]"
            )
        _print_rich_report(snapshots, report, debt_velocity)
