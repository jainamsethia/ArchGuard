"""archguard contract — manage architectural contracts."""

from __future__ import annotations

from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from archguard.config import EXIT_VIOLATION, EXIT_CONFIG_ERROR, EXIT_AUTH_ERROR
from archguard.contract.reinference import ReinferenceEngine
from archguard.utils.errors import format_error

contract_app: typer.Typer = typer.Typer(
    name="contract",
    help="Manage architectural contracts.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

_console: Console = Console()


@contract_app.command("list-pending")
def contract_list_pending(
    repo: Path = typer.Option(
        Path("."), "--repo", help="Repository root.",
    ),
) -> None:
    """List pending contract proposals."""
    try:
        from archguard.utils.validation import validate_repo_path, PathTraversalError
        from archguard.config import EXIT_CONFIG_ERROR
        repo = validate_repo_path(repo)
    except PathTraversalError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR)
        
    engine = ReinferenceEngine(repo.resolve())
    proposals = engine.list_pending()

    if not proposals:
        _console.print("No pending contract proposals.")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Module")
    table.add_column("Drift Score")
    table.add_column("Proposed Threshold")
    table.add_column("Proposed Budget")
    table.add_column("Timestamp")
    table.add_column("Age")

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    for p in proposals:
        try:
            ts = datetime.fromisoformat(p.proposal_timestamp)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_days = (now - ts).days
            age_str = f"{age_days}d"
        except (ValueError, TypeError):
            age_str = "?"

        table.add_row(
            p.module_name,
            f"{p.semantic_drift_score:.2f}",
            f"{p.proposed_drift_threshold:.2f}",
            str(p.proposed_coupling_budget),
            p.proposal_timestamp[:19],
            age_str,
        )

    _console.print(table)


@contract_app.command("accept")
def contract_accept(
    module: str = typer.Option(..., "--module", help="Module name."),
    repo_slug: str | None = typer.Option(
        None, "--repo-slug", help="Repository slug for GitHub mode.",
    ),
    branch: str = typer.Option(
        "main", "--branch", help="Branch for GitHub commit.",
    ),
    repo: Path = typer.Option(
        Path("."), "--repo", help="Repository root.",
    ),
) -> None:
    """Accept a pending contract proposal."""
    try:
        from archguard.utils.validation import validate_repo_path, PathTraversalError
        from archguard.config import EXIT_CONFIG_ERROR
        repo = validate_repo_path(repo)
    except PathTraversalError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR)
        
    repo_root = repo.resolve()
    engine = ReinferenceEngine(repo_root)

    github_client = None
    if repo_slug:
        try:
            from archguard.github.client import GitHubClient

            github_client = GitHubClient()
        except Exception:  # noqa: BLE001
            _console.print(format_error(
                "GitHub client unavailable. Use local mode (omit --repo-slug)."
            ))
            raise typer.Exit(EXIT_AUTH_ERROR)

    success = engine.accept_proposal(
        module, github_client=github_client,
        repo_slug=repo_slug, branch=branch,
    )

    if success:
        _console.print(
            f"Contract proposal for '{module}' accepted "
            f"and written to .archguard.yml"
        )
    else:
        _console.print(format_error(
            f"No pending proposal found for module '{module}'"
        ))
        raise typer.Exit(EXIT_CONFIG_ERROR)


@contract_app.command("reject")
def contract_reject(
    module: str = typer.Option(..., "--module", help="Module name."),
    repo: Path = typer.Option(
        Path("."), "--repo", help="Repository root.",
    ),
) -> None:
    """Reject a pending contract proposal."""
    try:
        from archguard.utils.validation import validate_repo_path, PathTraversalError
        from archguard.config import EXIT_CONFIG_ERROR
        repo = validate_repo_path(repo)
    except PathTraversalError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR)
        
    engine = ReinferenceEngine(repo.resolve())
    success = engine.reject_proposal(module)

    if success:
        _console.print(
            f"Contract proposal for '{module}' rejected and removed."
        )
    else:
        _console.print(format_error(
            f"No pending proposal found for module '{module}'"
        ))
        raise typer.Exit(EXIT_CONFIG_ERROR)


@contract_app.command("show")
def contract_show(
    module: str = typer.Option(..., "--module", help="Module name."),
    repo: Path = typer.Option(
        Path("."), "--repo", help="Repository root.",
    ),
) -> None:
    """Show a pending contract proposal."""
    try:
        from archguard.utils.validation import validate_repo_path, PathTraversalError
        from archguard.config import EXIT_CONFIG_ERROR
        repo = validate_repo_path(repo)
    except PathTraversalError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR)
        
    engine = ReinferenceEngine(repo.resolve())
    proposals = engine.list_pending()

    for p in proposals:
        if p.module_name == module:
            data = {
                "module_name": p.module_name,
                "proposed_paths": p.proposed_paths,
                "proposed_drift_threshold": p.proposed_drift_threshold,
                "proposed_coupling_budget": p.proposed_coupling_budget,
                "semantic_drift_score": p.semantic_drift_score,
                "proposal_timestamp": p.proposal_timestamp,
                "source_commit": p.source_commit,
            }
            yaml_str = yaml.dump(
                data, default_flow_style=False, sort_keys=False,
            )
            _console.print(Panel(
                yaml_str,
                title=f"Proposal: {module}",
                border_style="cyan",
            ))
            return

    _console.print(format_error(
        f"No pending proposal found for module '{module}'"
    ))
    raise typer.Exit(EXIT_CONFIG_ERROR)
