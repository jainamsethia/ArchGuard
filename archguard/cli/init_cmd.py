"""archguard init — full 5-phase onboarding wizard."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console



init_app: typer.Typer = typer.Typer(
    name="init",
    help="Initialize ArchGuard in a repository.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

_console: Console = Console()


@init_app.callback(invoke_without_command=True)
def init_command(
    ctx: typer.Context,
    repo: Path = typer.Option(
        Path("."),
        "--repo",
        help="Path to the repository root.",
    ),
    confirm_all: bool = typer.Option(
        False,
        "--confirm-all",
        help="Skip all interactive prompts.",
    ),
    force_ci: bool = typer.Option(
        False,
        "--force-ci",
        help="Bypass shallow clone check in CI.",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Resume from last saved checkpoint.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        help="Write contract to this path.",
    ),
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help="Skip LLM API calls entirely.",
    ),
    min_history_commits: int = typer.Option(
        5,
        "--min-history-commits",
        help="Minimum commits needed for co-change analysis (default: 5). "
        "Below this, falls back to directory-structure detection.",
    ),
    monorepo: bool = typer.Option(
        False,
        "--monorepo",
        help="Initialize each sub package separately",
    ),
    llm_init: bool = typer.Option(
        False,
        "--llm-init",
        help="Use Claude to generate contract from code structure (requires ANTHROPIC_API_KEY)",
    ),
) -> None:
    """Initialize ArchGuard in a repository with 5-phase onboarding."""
    try:
        from archguard.utils.validation import (
            validate_repo_path,
            validate_output_path,
            PathTraversalError,
        )
        from archguard.config import EXIT_CONFIG_ERROR

        repo = validate_repo_path(repo)
        if output is not None:
            output = validate_output_path(output)
    except PathTraversalError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR)

    repo_root = repo.resolve()

    if monorepo:
        from archguard.utils.monorepo import detect_subpackages

        packages = detect_subpackages(repo_root)
        if not packages:
            typer.echo("No sub-packages found. Run without --monorepo.")
            raise typer.Exit(1)

        has_error = False
        for pkg in packages:
            _console.print(
                f"\n[bold magenta]Initializing sub-package: {pkg.name}[/bold magenta]"
            )
            try:
                init_command(
                    ctx=ctx,
                    repo=pkg,
                    confirm_all=confirm_all,
                    force_ci=force_ci,
                    resume=resume,
                    output=pkg / ".archguard.yml",
                    no_llm=no_llm,
                    min_history_commits=min_history_commits,
                    monorepo=False,
                    llm_init=llm_init,
                )
            except typer.Exit as e:
                if e.exit_code != 0:
                    has_error = True
            except Exception as e:
                _console.print(f"[red]Error initializing {pkg.name}: {e}[/red]")
                has_error = True

        if has_error:
            raise typer.Exit(1)
        return

    from archguard.cli._init_dispatch import _run_init_cli
    _run_init_cli(
        ctx=ctx,
        repo_root=repo_root,
        output=output,
        confirm_all=confirm_all,
        force_ci=force_ci,
        resume=resume,
        no_llm=no_llm,
        min_history_commits=min_history_commits,
        llm_init=llm_init,
        _console=_console
    )
