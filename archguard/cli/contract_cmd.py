"""archguard contract — manage architectural contracts."""

import typer
from rich.console import Console

contract_app: typer.Typer = typer.Typer(
    name="contract",
    help="Manage architectural contracts.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

_console: Console = Console()


@contract_app.callback(invoke_without_command=True)
def contract_command() -> None:
    """Manage architectural contracts."""
    _console.print("[yellow]Not yet implemented.[/yellow]")
