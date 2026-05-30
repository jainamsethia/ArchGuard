"""archguard profiles list — display configuration profiles."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from archguard.profiles.defaults import PROFILES

profiles_app: typer.Typer = typer.Typer(
    name="profiles",
    help="Manage and list ArchGuard configuration profiles.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

_console = Console()


@profiles_app.command("list")
def list_profiles() -> None:
    """List all available configuration profiles and their default thresholds."""
    table = Table(title="ArchGuard Configuration Profiles")
    table.add_column("Profile", style="cyan", no_wrap=True)
    table.add_column("Description", style="white")
    table.add_column("Health", style="green")
    table.add_column("Coupling", style="yellow")
    table.add_column("Duplication", style="magenta")
    table.add_column("Cohesion", style="blue")

    for name, p in PROFILES.items():
        t = p["thresholds"]
        table.add_row(
            name,
            p["description"],
            f">={t['min_health_score']}%",
            f"<={t['max_coupling']} imports",
            f"<={t['max_duplication'] * 100:.0f}%",
            f">={t['min_cohesion'] * 100:.0f}%",
        )

    _console.print()
    _console.print(table)
    _console.print()
