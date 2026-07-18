import typer
from rich.console import Console

console = Console()


def vprint(message: str, ctx: typer.Context | None = None, level: str = "info") -> None:
    """Print message respecting --verbose/--quiet flags."""
    if ctx:
        quiet = ctx.obj.get("quiet", False)
        verbose = ctx.obj.get("verbose", False)

        if quiet and level != "error":
            return
        if level == "debug" and not verbose:
            return

    console.print(message)
