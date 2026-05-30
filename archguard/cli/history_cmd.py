import typer
from pathlib import Path
from rich.console import Console
from rich.table import Table
import json
from datetime import datetime, timezone, timedelta
from archguard.config import AUDIT_EVENT_ANALYSIS, AUDIT_LOG_FILENAME


def _sparkline(scores: list[float]) -> str:
    bars = "▁▂▃▄▅▆▇█"
    if not scores:
        return ""
    min_s, max_s = min(scores), max(scores)
    rng = max_s - min_s or 1
    return "".join(bars[int((s - min_s) / rng * 7)] for s in scores)


def show_history(
    format: str = typer.Option("table", help="Output format: table, trend, json"),
    limit: int = typer.Option(20, help="Number of recent runs to show"),
    since: int = typer.Option(None, help="Filter to the last N days of runs"),
    module: str | None = typer.Option(None, help="Filter by module name"),
    audit_log: Path = typer.Option(Path(AUDIT_LOG_FILENAME), help="Path to audit log"),
) -> None:
    """Show ArchDebt score trend across recent analysis runs."""
    console = Console()

    if not audit_log.exists():
        if format == "json":
            console.print(json.dumps({"error": "No audit log found.", "runs": []}))
        else:
            console.print(
                "[yellow]No audit history found. Run `archguard analyze` first.[/yellow]"
            )
        raise typer.Exit(1 if format == "json" else 0)

    entries = []
    with open(audit_log, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Filter to analysis events
    analysis_runs = [e for e in entries if e.get("event") == AUDIT_EVENT_ANALYSIS]

    # Process timestamps and grades for each run to unify structure
    runs = []
    for event in analysis_runs:
        ts_str = event.get("timestamp")
        if not ts_str:
            continue
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

        runs.append(
            {
                "timestamp": dt,
                "score": float(
                    event.get(
                        "score",
                        event.get("archdebt", {}).get("composite_score", 0.0) * 100,
                    )
                ),
                "grade": event.get(
                    "grade",
                    event.get("band", event.get("archdebt", {}).get("band", "UNKNOWN")),
                ),
                "violation_count": int(
                    event.get("violation_count", len(event.get("violations", [])))
                ),
                "pr_number": str(event.get("pr_number", "local")),
            }
        )

    if since is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since)
        runs = [r for r in runs if r["timestamp"] >= cutoff]

    if module:
        # Currently no module-specific scores in top-level audit log
        pass

    if not runs:
        if format == "json":
            console.print(json.dumps({"runs": []}))
        else:
            console.print(
                "[yellow]No completed analysis runs matching criteria.[/yellow]"
            )
        raise typer.Exit(0)

    runs.sort(key=lambda r: r["timestamp"])
    runs = runs[-limit:]
    scores = [r["score"] for r in runs]

    if format == "json":
        console.print(
            json.dumps(
                {
                    "runs": [
                        {
                            "timestamp": r["timestamp"].isoformat(),
                            "score": r["score"],
                            "grade": str(r["grade"]),
                            "pr_number": r["pr_number"],
                            "violation_count": r["violation_count"],
                        }
                        for r in runs
                    ],
                    "sparkline": _sparkline(scores),
                },
                indent=2,
            )
        )
        return

    elif format == "trend":
        recent_runs = list(reversed(runs))
        table = Table(title=f"Architecture Health Trends (last {len(runs)} runs)")
        table.add_column("Timestamp", justify="left", style="cyan", no_wrap=True)
        table.add_column("Score", justify="right", style="magenta")
        table.add_column("Grade", justify="center", style="green")
        table.add_column("Violations", justify="right", style="yellow")

        for r in recent_runs:
            ts_display = r["timestamp"].strftime("%Y-%m-%d %H:%M")
            table.add_row(
                ts_display,
                f"{r['score']:.1f}",
                str(r["grade"]),
                str(r["violation_count"]),
            )

        console.print()
        console.print(table)
        console.print()

        if len(runs) > 1:
            diff = scores[-1] - scores[0]
            direction = "improving" if diff >= 0 else "degrading"
            sign = "+" if diff >= 0 else ""
            arrow = "↑" if diff >= 0 else "↓"
            console.print(
                f"Trend: {arrow} {sign}{diff:.1f} points over {len(runs)} runs ({direction})"
            )
        else:
            console.print("Trend: Insufficient data for trend line.")

        spark = _sparkline(scores)
        console.print(f"Score history: {spark}")
        console.print()

    else:
        # Default table view
        table = Table(title=f"ArchDebt Trend (last {len(runs)} runs)", show_header=True)
        table.add_column("Date", style="dim")
        table.add_column("PR", justify="center")
        table.add_column("Score", justify="right")
        table.add_column("Band", justify="center")
        table.add_column("Trend", justify="center")

        prev_score = None
        for run in runs:
            score = run["score"]
            band = str(run["grade"])
            date = run["timestamp"].strftime("%Y-%m-%d")
            pr = run["pr_number"]

            if prev_score is not None:
                delta = score - prev_score
                trend = (
                    "[red]↑[/red]"
                    if delta > 0.01
                    else "[green]↓[/green]"
                    if delta < -0.01
                    else "[dim]→[/dim]"
                )
            else:
                trend = "[dim]—[/dim]"

            band_color = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}.get(
                band, "white"
            )
            table.add_row(
                date, pr, f"{score:.3f}", f"[{band_color}]{band}[/{band_color}]", trend
            )

            prev_score = score

        console.print(table)

        console.print("\n[bold]Score Trend:[/bold]")
        min_v, max_v = min(scores), max(scores)
        console.print(f"  {_sparkline(scores)}  [dim]{min_v:.2f} → {max_v:.2f}[/dim]")
