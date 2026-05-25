"""archguard suppress — manage violation suppressions."""

import typer
from rich.console import Console

suppress_app: typer.Typer = typer.Typer(
    name="suppress",
    help="Manage architectural violation suppressions.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

_console: Console = Console()


@suppress_app.callback(invoke_without_command=True)
def suppress_command() -> None:
    """Create or manage suppressions."""
    _console.print("[yellow]Not yet implemented.[/yellow]")
