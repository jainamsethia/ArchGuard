import json
import typing

import typer
from rich.console import Console

from archguard.audit.logger import AuditLogger
from archguard.config import EXIT_CONFIG_ERROR, EXIT_SUCCESS
from archguard.utils.validation import PathTraversalError, validate_repo_path

diff_app = typer.Typer(
    name="diff",
    help="Show what changed between the last two analysis runs.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


def get_key(v: typing.Any) -> str:
    """Generate a unique key for a violation."""
    return f"{v.get('layer', '')}:{v.get('file', '')}:{v.get('message', '')}"


@diff_app.callback(invoke_without_command=True)
def diff_cmd(
    repo: str = typer.Option(".", "--repo", help="Path to the repository root."),
    runs: int = typer.Option(2, help="Compare last N runs (default: last 2)"),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
) -> None:
    """Show what changed between the last two analysis runs."""
    try:
        repo_path = validate_repo_path(repo)
    except PathTraversalError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR)

    audit_log_path = repo_path / ".archguard-cache" / "audit.jsonl"
    logger = AuditLogger(audit_log_path)

    recent_runs = logger.read_last_n_runs(n=runs)
    if len(recent_runs) < 2:
        typer.echo("Need at least 2 runs to diff. Run 'archguard analyze' first.")
        raise typer.Exit(EXIT_SUCCESS)

    old_run, new_run = recent_runs[-2], recent_runs[-1]

    old_violations = {get_key(v) for v in old_run.get("violations", [])}
    new_violations = {get_key(v) for v in new_run.get("violations", [])}

    resolved = old_violations - new_violations  # Fixed since last run
    introduced = new_violations - old_violations  # New since last run
    persisting = old_violations & new_violations  # Still present

    score_delta = new_run.get("score", 0.0) - old_run.get("score", 0.0)

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "score_delta": score_delta,
                    "introduced": list(introduced),
                    "resolved": list(resolved),
                    "persisting": list(persisting),
                }
            )
        )
        return

    # Rich output
    console = Console()
    console.print(
        f"\n[bold]ArchDebt Diff[/bold]: {old_run.get('timestamp', 'unknown')} -> {new_run.get('timestamp', 'unknown')}"
    )
    score_color = "red" if score_delta > 0 else "green"
    console.print(f"Score: [{score_color}]{score_delta:+.3f}[/{score_color}]")

    if introduced:
        console.print(f"\n[red]🆕 New violations ({len(introduced)}):[/red]")
        for v in sorted(introduced):
            console.print(f"  • {v}")

    if resolved:
        console.print(f"\n[green][OK] Resolved violations ({len(resolved)}):[/green]")
        for v in sorted(resolved):
            console.print(f"  • {v}")
