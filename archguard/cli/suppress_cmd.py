"""archguard suppress — manage violation suppressions."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from archguard.config import EXIT_OK, EXIT_VIOLATION
from archguard.suppression.store import SuppressionStore, SuppressionValidationError
from archguard.utils.errors import format_error

suppress_app: typer.Typer = typer.Typer(
    name="suppress",
    help="Manage architectural violation suppressions.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

_console: Console = Console()


@suppress_app.command("add")
def suppress_add(
    module: str = typer.Option(..., "--module", help="Module name."),
    layer: int = typer.Option(..., "--layer", help="Layer (1–4)."),
    message: str = typer.Option(..., "--message", help="Violation message."),
    reason: str = typer.Option(..., "--reason", help="Suppression reason."),
    expires: str | None = typer.Option(
        None, "--expires", help="Expiry date (ISO8601).",
    ),
    pr: int | None = typer.Option(None, "--pr", help="PR number."),
    repo: Path = typer.Option(
        Path("."), "--repo", help="Repository root.",
    ),
) -> None:
    """Add a violation suppression."""
    store = SuppressionStore(repo.resolve())
    try:
        suppression = store.add(
            module=module,
            layer=layer,
            message=message,
            reason=reason,
            expires_at=expires,
            pr_number=pr,
        )
    except SuppressionValidationError as exc:
        _console.print(format_error(str(exc)))
        raise typer.Exit(EXIT_VIOLATION) from exc

    _console.print(
        f"Suppression created: {suppression.id[:8]} "
        f"for {module} L{layer}"
    )


@suppress_app.command("list")
def suppress_list(
    json_output: bool = typer.Option(
        False, "--json", help="Output JSON.",
    ),
    include_inactive: bool = typer.Option(
        False, "--include-inactive", help="Include inactive suppressions.",
    ),
    repo: Path = typer.Option(
        Path("."), "--repo", help="Repository root.",
    ),
) -> None:
    """List suppressions."""
    store = SuppressionStore(repo.resolve())
    suppressions = store.list_all(include_inactive=include_inactive)

    if not suppressions:
        _console.print("No active suppressions.")
        return

    if json_output:
        _console.print(store.to_json(suppressions))
    else:
        _console.print(store.to_columnar_table(suppressions))


@suppress_app.command("migrate")
def suppress_migrate(
    from_module: str = typer.Option(
        ..., "--from", help="Old module name.",
    ),
    to_module: str = typer.Option(
        ..., "--to", help="New module name.",
    ),
    repo: Path = typer.Option(
        Path("."), "--repo", help="Repository root.",
    ),
) -> None:
    """Migrate suppressions from one module to another."""
    store = SuppressionStore(repo.resolve())
    count = store.migrate_module(from_module, to_module)
    _console.print(
        f"Migrated {count} suppression(s) from '{from_module}' to '{to_module}'"
    )


@suppress_app.command("orphans")
def suppress_orphans(
    confirm_all: bool = typer.Option(
        False, "--confirm-all", help="Skip confirmation prompt.",
    ),
    repo: Path = typer.Option(
        Path("."), "--repo", help="Repository root.",
    ),
) -> None:
    """Detect and display orphaned suppressions."""
    repo_root = repo.resolve()
    store = SuppressionStore(repo_root)

    # Load active module names from contract
    try:
        from archguard.contract.loader import load_contract

        contract = load_contract(repo_root)
        active_modules = [m["name"] for m in contract.get("modules", [])]
    except Exception:  # noqa: BLE001
        _console.print(format_error("Could not load contract."))
        raise typer.Exit(EXIT_VIOLATION)

    orphans = store.detect_orphans(active_modules)
    if not orphans:
        _console.print("No orphaned suppressions found.")
        return

    _console.print(store.to_columnar_table(orphans))

    if confirm_all or typer.confirm("Mark all as inactive?", default=False):
        count = store.mark_orphans([o.id for o in orphans])
        _console.print(f"Marked {count} suppression(s) as inactive.")
