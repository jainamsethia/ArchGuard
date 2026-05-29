"""archguard status — display current configuration and health."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from archguard.config import AUDIT_LOG_FILENAME
from archguard.contract.loader import load_contract
from archguard.contract.validator import ContractValidationError
from archguard.utils.errors import ConfigError, format_error

status_app: typer.Typer = typer.Typer(
    name="status",
    help="Show current ArchGuard configuration and health.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

_console: Console = Console()


def _count_audit_entries(repo_root: Path) -> int | None:
    """Count entries in the audit log, or return None if it doesn't exist."""
    audit_path = repo_root / AUDIT_LOG_FILENAME
    if not audit_path.is_file():
        return None
    try:
        with audit_path.open("r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(f"Non-critical failure counting audit entries: {e}")
        return None


def _render_table(data: dict[str, Any], audit_count: int | None) -> None:
    """Render the status output as a Rich table."""
    _console.print()
    _console.print(f"[bold cyan]Schema version:[/bold cyan] {data['version']}")

    modules: list[dict[str, Any]] = data.get("modules", [])
    _console.print(f"[bold cyan]Modules defined:[/bold cyan] {len(modules)}")
    _console.print()

    table = Table(title="Modules", show_header=True, header_style="bold magenta")
    table.add_column("Name", style="green")
    table.add_column("Path", style="dim")

    for module in modules:
        table.add_row(
            module.get("name", ""),
            module.get("path", ""),
        )

    _console.print(table)
    _console.print()

    fail_threshold: Any = data.get("fail_threshold")
    warn_threshold: Any = data.get("warn_threshold")
    if fail_threshold is not None:
        _console.print(f"[bold cyan]Fail threshold:[/bold cyan] {fail_threshold}")
    if warn_threshold is not None:
        _console.print(f"[bold cyan]Warn threshold:[/bold cyan] {warn_threshold}")

    if audit_count is not None:
        _console.print(f"[bold cyan]Audit log:[/bold cyan] {audit_count} entries")
    else:
        _console.print("[bold cyan]Audit log:[/bold cyan] [dim]not found[/dim]")

    _console.print()


def _render_json(data: dict[str, Any], audit_count: int | None) -> None:
    """Render the status output as JSON."""
    output: dict[str, Any] = {
        "version": data.get("version"),
        "module_count": len(data.get("modules", [])),
        "modules": [
            {"name": m.get("name"), "path": m.get("path", "")}
            for m in data.get("modules", [])
        ],
        "fail_threshold": data.get("fail_threshold"),
        "warn_threshold": data.get("warn_threshold"),
        "audit_log_exists": audit_count is not None,
        "audit_log_entries": audit_count,
    }
    _console.print_json(json.dumps(output))


@status_app.callback(invoke_without_command=True)
def status_command(
    repo: Path = typer.Option(
        Path("."),
        "--repo",
        help="Path to the repository root.",
        exists=False,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON instead of a Rich table.",
    ),
) -> None:
    """Show current ArchGuard configuration and health."""
    try:
        from archguard.utils.validation import validate_repo_path, PathTraversalError
        from archguard.config import EXIT_CONFIG_ERROR
        repo = validate_repo_path(repo)
    except PathTraversalError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR)
        
    repo_root = repo.resolve()

    try:
        data = load_contract(repo_root)
    except ConfigError as exc:
        _console.print(format_error(exc.message))
        raise typer.Exit(code=exc.exit_code) from exc
    except ContractValidationError as exc:
        for err in exc.errors:
            _console.print(format_error(err))
        raise typer.Exit(code=1) from exc

    audit_count = _count_audit_entries(repo_root)

    if json_output:
        _render_json(data, audit_count)
    else:
        _render_table(data, audit_count)
