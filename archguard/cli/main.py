"""Root Typer application - registers all subcommands."""

import typer
from pathlib import Path
from rich.console import Console

from archguard.cli.analyze_cmd import analyze_app
from archguard.cli.contract_cmd import contract_app
from archguard.cli.init_cmd import init_app
from archguard.cli.status_cmd import status_app
from archguard.cli.suppress_cmd import suppress_app
from archguard.cli.github_sync_cmd import github_sync_app
from archguard.cli.report_cmd import report_app
from archguard.cli.profiles_cmd import profiles_app
from archguard.cli.sync_cmd import sync_cache
from archguard.cli.history_cmd import show_history
from archguard.cli.diff_cmd import diff_app
from archguard.cli.dashboard_cmd import dashboard_app
from archguard.cli.fitness_cmd import fitness_app
from archguard.cli.history_analyze_cmd import history_analyze_app
from archguard.observability.logger import configure_logging

app: typer.Typer = typer.Typer(
    name="archguard",
    help="Architectural drift detector for Python codebases.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console: Console = Console()

# Register all subcommand apps
app.add_typer(init_app, name="init")
app.add_typer(analyze_app, name="analyze")
app.add_typer(suppress_app, name="suppress")
app.add_typer(contract_app, name="contract")
app.add_typer(status_app, name="status")
app.add_typer(github_sync_app, name="github-sync")
app.add_typer(report_app, name="report")
app.add_typer(profiles_app, name="profiles")
app.command("sync")(sync_cache)
app.command("history")(show_history)
app.add_typer(diff_app, name="diff")
app.add_typer(dashboard_app, name="dashboard")
app.add_typer(fitness_app, name="fitness")
app.add_typer(history_analyze_app, name="history-analyze")


@app.command("cache-check")
def cache_check_cmd(repair: bool = typer.Option(False, "--repair")) -> None:
    """Check and optionally repair the embedding cache."""
    from pathlib import Path

    import json
    import sqlite3

    cache_dir = Path(".archguard-cache")
    checkpoints_dir = Path(".archguard-checkpoints")

    corrupt_files = []
    healthy_files = 0

    if cache_dir.exists():
        for db_file in cache_dir.glob("*.db"):
            try:
                # Basic SQLite integrity check
                with sqlite3.connect(db_file) as conn:
                    cursor = conn.cursor()
                    cursor.execute("PRAGMA integrity_check;")
                    result = cursor.fetchone()
                    if not result or result[0] != "ok":
                        raise ValueError(f"Integrity check failed: {result}")
                healthy_files += 1
            except Exception as e:
                console.print(f"[yellow]Corrupted SQLite DB found: {db_file} ({e})[/yellow]")
                corrupt_files.append(db_file)

    if checkpoints_dir.exists():
        for json_file in checkpoints_dir.glob("*.json"):
            try:
                with open(json_file, encoding="utf-8") as f:
                    json.load(f)
                healthy_files += 1
            except Exception as e:
                console.print(f"[yellow]Corrupted JSON found: {json_file} ({e})[/yellow]")
                corrupt_files.append(json_file)

    if not corrupt_files:
        console.print(f"[green]Cache OK: {healthy_files} healthy files found, 0 corrupted.[/green]")
        return

    if repair:
        for corrupt_file in corrupt_files:
            corrupt_file.unlink(missing_ok=True)
            console.print(f"[green]Removed corrupted file: {corrupt_file}[/green]")
        console.print("[green]Repair complete. Run archguard analyze to rebuild missing cache files.[/green]")
    else:
        console.print(f"[red]Found {len(corrupt_files)} corrupted files. Run with --repair to safely remove them.[/red]")
        raise typer.Exit(1)


@app.command("trends", hidden=True, deprecated=True)
def trends_cmd(
    json_output: bool = typer.Option(
        False, "--json", help="Output raw JSON instead of the rich table."
    ),
    since: int = typer.Option(
        None, "--since", help="Filter to the last N days of runs."
    ),
) -> None:
    """Deprecated: Use history --format trend."""
    typer.echo("Warning: 'trends' is deprecated, use 'history --format trend'")
    fmt = "json" if json_output else "trend"
    from archguard.config import AUDIT_LOG_FILENAME

    show_history(
        format=fmt,
        limit=20,
        since=since,
        module=None,
        audit_log=Path(AUDIT_LOG_FILENAME),
    )


@app.callback(invoke_without_command=True)
def cli(
    ctx: typer.Context,
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed output including debug information. Mutually exclusive with --quiet (but --quiet wins).",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress all output except errors and the final result. Mutually exclusive with --verbose (but --quiet wins).",
    ),
    version: bool = typer.Option(False, "--version", help="Show the version and exit."),
) -> None:
    if version:
        import importlib.metadata

        try:
            v = importlib.metadata.version("archguard")
        except importlib.metadata.PackageNotFoundError:
            v = "unknown"
        typer.echo(f"archguard, version {v}")
        raise typer.Exit()

    configure_logging(verbose=verbose, quiet=quiet)
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet


def main() -> None:
    """Entry point for the archguard CLI."""
    app()
