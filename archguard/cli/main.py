"""Root Typer application — registers all subcommands."""

import typer
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
    show_history(format=fmt, limit=20, since=since, module=None, audit_log=Path(AUDIT_LOG_FILENAME))


@app.callback(invoke_without_command=True)
def cli(
    ctx: typer.Context,
    verbose: bool = typer.Option(
        False, "--verbose", "-v", 
        help="Show detailed output including debug information. Mutually exclusive with --quiet (but --quiet wins)."
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", 
        help="Suppress all output except errors and the final result. Mutually exclusive with --verbose (but --quiet wins)."
    ),
    version: bool = typer.Option(
        False, "--version", 
        help="Show the version and exit."
    ),
) -> None:
    if version:
        import importlib.metadata
        try:
            v = importlib.metadata.version("archguard")
        except importlib.metadata.PackageNotFoundError:
            v = "unknown"
        typer.echo(f"archguard, version {v}")
        raise typer.Exit()
        
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet

def main() -> None:
    """Entry point for the archguard CLI."""
    app()
