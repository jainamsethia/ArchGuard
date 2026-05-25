"""Root Typer application — registers all subcommands."""

import typer
from rich.console import Console

from archguard.cli.analyze_cmd import analyze_app
from archguard.cli.contract_cmd import contract_app
from archguard.cli.init_cmd import init_app
from archguard.cli.status_cmd import status_app
from archguard.cli.suppress_cmd import suppress_app

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


def main() -> None:
    """Entry point for the archguard CLI."""
    app()
