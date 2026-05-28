"""archguard trends — visualizes architecture health over time."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from archguard.config import AUDIT_LOG_FILENAME

trends_app: typer.Typer = typer.Typer(
    name="trends",
    help="Visualize architectural health trends from the audit log.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

_console = Console()

def sparkline(scores: list[float]) -> str:
    """Generate an ASCII sparkline from score history."""
    bars = "▁▂▃▄▅▆▇█"
    if not scores:
        return ""
    min_s, max_s = min(scores), max(scores)
    rng = max_s - min_s or 1
    return "".join(bars[int((s - min_s) / rng * 7)] for s in scores)

@trends_app.callback(invoke_without_command=True)
def trends_cmd(
    ctx: typer.Context,
    json_output: bool = typer.Option(
        False, "--json", help="Output raw JSON instead of the rich table."
    ),
    since: int = typer.Option(
        None, "--since", help="Filter to the last N days of runs."
    ),
) -> None:
    """Read the audit log and display recent analysis run scores and trends."""
    log_path = Path(AUDIT_LOG_FILENAME)
    if not log_path.exists():
        if json_output:
            _console.print(json.dumps({"error": "No audit log found.", "runs": []}))
        else:
            _console.print("[yellow]No audit log found. Run `archguard analyze` to generate data.[/yellow]")
        raise typer.Exit(1)

    runs = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if event.get("event") == "analysis_run":
                        # Required fields: timestamp, score, grade, violation_count
                        # Sometimes structure might vary slightly, fallback if missing
                        ts_str = event.get("timestamp")
                        if not ts_str:
                            continue
                        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        
                        runs.append({
                            "timestamp": dt,
                            "score": float(event.get("score", event.get("archdebt", {}).get("composite_score", 0.0) * 100)),
                            "grade": event.get("grade", event.get("archdebt", {}).get("band", "UNKNOWN")),
                            "violation_count": int(event.get("violation_count", len(event.get("violations", [])))),
                        })
                except Exception:
                    pass
    except Exception as e:
        if json_output:
            _console.print(json.dumps({"error": f"Failed to read audit log: {e}"}))
        else:
            _console.print(f"[red]Failed to read audit log: {e}[/red]")
        raise typer.Exit(1)

    if since is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since)
        runs = [r for r in runs if r["timestamp"] >= cutoff]

    if not runs:
        if json_output:
            _console.print(json.dumps({"runs": []}))
        else:
            _console.print("[yellow]No analysis runs found matching the criteria.[/yellow]")
        raise typer.Exit(0)

    # Sort chronological
    runs.sort(key=lambda r: r["timestamp"])
    scores = [r["score"] for r in runs]
    
    if json_output:
        _console.print(json.dumps({
            "runs": [
                {
                    "timestamp": r["timestamp"].isoformat(),
                    "score": r["score"],
                    "grade": str(r["grade"]),
                    "violation_count": r["violation_count"],
                }
                for r in runs
            ],
            "sparkline": sparkline(scores)
        }, indent=2))
        return

    # Table output
    # Reverse runs for the table to show newest first, up to 10
    recent_runs = list(reversed(runs))[:10]

    table = Table(title="Architecture Health Trends")
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
            str(r["violation_count"])
        )

    _console.print()
    _console.print(table)
    _console.print()

    if len(runs) > 1:
        diff = scores[-1] - scores[0]
        direction = "improving" if diff >= 0 else "degrading"
        sign = "+" if diff >= 0 else ""
        arrow = "↑" if diff >= 0 else "↓"
        _console.print(f"Trend: {arrow} {sign}{diff:.1f} points over {len(runs)} runs ({direction})")
    else:
        _console.print("Trend: Insufficient data for trend line.")

    spark = sparkline(scores)
    _console.print(f"Score history: {spark}")
    _console.print()
