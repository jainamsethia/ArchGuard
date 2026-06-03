from __future__ import annotations
import typer
from rich.console import Console
from archguard.contract.writer import _infer_path

_console: Console = Console()

def _interactive_review(
    communities: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Interactive module review for TTY sessions."""
    reviewed: dict[str, list[str]] = {}

    for name, files in communities.items():
        _console.print()
        _console.print(f"[bold]Module: {name}[/bold] ({len(files)} files)")
        path = _infer_path(files)
        _console.print(f"Paths: {path}")

        response = typer.prompt("Accept this module? [Y/n/rename]", default="Y")
        response = response.strip().lower()

        if response in ("y", ""):
            reviewed[name] = files
        elif response == "n":
            _console.print(f"[dim]Skipped {name}[/dim]")
        elif response.startswith("rename"):
            new_name = typer.prompt("New name")
            reviewed[new_name.strip()] = files
        else:
            reviewed[name] = files

    return reviewed
