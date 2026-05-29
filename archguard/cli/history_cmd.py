import typer
from pathlib import Path
from rich.console import Console
from rich.table import Table
import json
from archguard.config import AUDIT_EVENT_ANALYSIS

def _print_sparkline(console, values: list[float]):
    chars = " ▂▃▄▅▆▇█"
    if not values:
        return
    min_v, max_v = min(values), max(values)
    span = max_v - min_v or 1
    bar = "".join(chars[int((v - min_v) / span * 7)] for v in values)
    console.print(f"  {bar}  [dim]{min_v:.2f} → {max_v:.2f}[/dim]")

def show_history(
    limit: int = typer.Option(10, help="Number of recent runs to show"),
    audit_log: Path = typer.Option(Path(".archguard-cache/audit.jsonl"), help="Path to audit log"),
):
    """Show ArchDebt score trend across recent analysis runs."""
    console = Console()
    
    if not audit_log.exists():
        console.print("[yellow]No audit history found. Run `archguard analyze` first.[/yellow]")
        raise typer.Exit(0)
        
    entries = []
    with open(audit_log) as f:
        for line in f:
            try:
                entries.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                continue
                
    # Filter to analysis events and take last N
    analysis_runs = [e for e in entries if e.get("event") == AUDIT_EVENT_ANALYSIS][-limit:]
    
    if not analysis_runs:
        console.print("[yellow]No completed analysis runs in audit log.[/yellow]")
        raise typer.Exit(0)
        
    # Print trend table
    table = Table(title=f"ArchDebt Trend (last {len(analysis_runs)} runs)", show_header=True)
    table.add_column("Date", style="dim")
    table.add_column("PR", justify="center")
    table.add_column("Score", justify="right")
    table.add_column("Band", justify="center")
    table.add_column("Trend", justify="center")
    
    prev_score = None
    for run in analysis_runs:
        score = run.get("score", 0)
        band = run.get("band", "?")
        date = run.get("timestamp", "")[:10]
        pr = str(run.get("pr_number", "local"))
        
        if prev_score is not None:
            delta = score - prev_score
            trend = "[red]↑[/red]" if delta > 0.01 else "[green]↓[/green]" if delta < -0.01 else "[dim]→[/dim]"
        else:
            trend = "[dim]—[/dim]"
            
        band_color = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}.get(band, "white")
        table.add_row(date, pr, f"{score:.3f}", f"[{band_color}]{band}[/{band_color}]", trend)
        
        prev_score = score
        
    console.print(table)
    
    # Print simple ASCII sparkline
    scores = [r.get("score", 0) for r in analysis_runs]
    console.print("\n[bold]Score Trend:[/bold]")
    _print_sparkline(console, scores)
