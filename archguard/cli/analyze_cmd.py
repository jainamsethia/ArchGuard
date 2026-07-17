"""archguard analyze - full architectural drift analysis."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console


from archguard.cli._analyze_options import AnalyzeOptions as AnalyzeOptions


_console = Console()

analyze_app: typer.Typer = typer.Typer(
    name="analyze",
    help="Analyze the codebase for architectural drift.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@analyze_app.callback(invoke_without_command=True)
def analyze_command(
    ctx: typer.Context,
    repo: Path = typer.Option(
        Path("."),
        "--repo",
        help="Path to the repository root.",
    ),
    pr: int | None = typer.Option(
        None,
        "--pr",
        "--pr-number",
        help="Pull request number.",
    ),
    repo_slug: str | None = typer.Option(
        None,
        "--repo-slug",
        help="Repository slug (e.g. myorg/myrepo).",
    ),
    github_url: str | None = typer.Option(
        None,
        "--github-url",
        help="Analyze a remote GitHub repository (clones to a temp directory).",
    ),
    profile: str = typer.Option(
        None,
        "--profile",
        help="Use a preset configuration profile (strict, lenient, ci).",
    ),
    changed_files: str | None = typer.Option(
        None,
        "--changed-files",
        help="Comma-separated file list or @filename.",
    ),
    skip_explanation: bool = typer.Option(
        False,
        "--skip-explanation",
        help="Skip Layer 4 LLM explanation.",
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="Force full corpus rebuild.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output JSON instead of Rich table.",
    ),
    output_format: str = typer.Option(
        None,
        "--output",
        help="Output format (e.g., json)",
    ),
    fail_on_warn: bool = typer.Option(
        False,
        "--fail-on-warn",
        help="Exit 1 on Watch band too.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Analyze but don't post PR comment.",
    ),
    incremental: bool = typer.Option(
        False,
        "--incremental",
        help="Run incrementally, analyzing only changed files.",
    ),
    no_incremental: bool = typer.Option(
        False,
        "--no-incremental",
        help="Force full analysis, ignoring the incremental cache.",
    ),
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help="Skip LLM API calls entirely.",
    ),
    out_file: Path | None = typer.Option(
        None,
        "--out-file",
        help="Write JSON result to this file path (for CI/CD pipelines)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show full debug logs.",
    ),
    fail_fast: bool = typer.Option(
        False,
        "--fail-fast",
        help="Exit immediately if a layer breaches the fail threshold",
    ),
    metrics_flag: bool = typer.Option(
        False, "--metrics", help="Display performance metrics breakdown"
    ),
    watch: bool = typer.Option(
        False,
        "--watch",
        "-w",
        help="Re-run on file changes",
    ),
    monorepo: bool = typer.Option(
        False,
        "--monorepo",
        help="Analyze each sub package separately",
    ),
    fail_threshold: float | None = typer.Option(
        None,
        "--fail-threshold",
        help="Override the fail threshold from the config file.",
    ),
) -> None:
    """Run architectural drift analysis."""
    if verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)

    try:
        from archguard.utils.validation import (
            validate_repo_path,
            validate_output_path,
            PathTraversalError,
        )
        from archguard.config import EXIT_CONFIG_ERROR

        repo = validate_repo_path(repo)
        if out_file is not None:
            out_file = validate_output_path(out_file)
    except PathTraversalError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR)

    import tempfile
    import subprocess
    import atexit
    import shutil

    if github_url:
        temp_dir = tempfile.mkdtemp(prefix="archguard_")
        atexit.register(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        from archguard.cli._analyze_core import _status
        _dummy_opts = type("Opts", (), {"output_format": output_format})()
        _status(f"[bold blue]Cloning {github_url} into temporary directory...[/bold blue]", _dummy_opts)
        res = subprocess.run(["git", "clone", "--depth", "1", github_url, temp_dir], capture_output=True, text=True)
        if res.returncode != 0:
            _status(f"[bold red]Failed to clone repository:[/bold red] {res.stderr}", _dummy_opts)
            raise typer.Exit(1)
        repo = Path(temp_dir)
        _status("[green]Successfully cloned repository.[/green]", _dummy_opts)

    if output_format == "json":
        json_output = True

    try:
        opts = AnalyzeOptions(
            ctx=ctx,
            repo=repo,
            pr_number=pr,
            repo_slug=repo_slug,
            profile=profile,
            changed_files=changed_files,
            skip_explanation=skip_explanation,
            full=full,
            json_output=json_output,
            fail_on_warn=fail_on_warn,
            dry_run=dry_run,
            incremental=incremental,
            no_incremental=no_incremental,
            no_llm=no_llm,
            out_file=out_file,
            fail_fast=fail_fast,
            monorepo=monorepo,
            fail_threshold=fail_threshold,
            watch=watch,
            metrics_flag=metrics_flag,
            verbose=verbose,
        )
        from archguard.cli._analyze_dispatch import _run_analyze_cli

        # We will catch typer.Exit to run dep health before exiting
        caught_exit = None
        try:
            _run_analyze_cli(
                opts,
                json_output,
                watch,
                monorepo,
                verbose,
                repo,
                pr,
                repo_slug,
                profile,
                changed_files,
                skip_explanation,
                full,
                fail_on_warn,
                dry_run,
                incremental,
                no_incremental,
                no_llm,
                out_file,
                fail_fast,
                fail_threshold,
            )
        except typer.Exit as e:
            caught_exit = e

        # Run Dependency Health Score calculation
        from archguard.analysis.deps import analyze_dependencies
        from rich.panel import Panel
        from rich.table import Table

        dep_res = analyze_dependencies(repo)

        if not json_output:
            if dep_res.skipped:
                from archguard.cli._analyze_core import _status
                _status(
                    f"[dim]Dependency Health skipped: {dep_res.skip_reason}[/dim]",
                    opts
                )
            else:
                table = Table(
                    title="Dependency Vulnerabilities (pip-audit)", show_header=True
                )
                table.add_column("Package", style="cyan")
                table.add_column("Version", style="blue")
                table.add_column("CVE/GHSA", style="red")
                table.add_column("Description", overflow="fold")

                for v in dep_res.vulnerabilities[:5]:  # Top 5
                    table.add_row(
                        v.package, v.version, v.vulnerability_id, v.description
                    )

                summary = (
                    f"[bold]Score:[/bold] {dep_res.score:.1f}/100\n"
                    f"[bold]Scanned:[/bold] {dep_res.scanned_packages} packages\n"
                    f"[bold]Vulnerabilities:[/bold] {len(dep_res.vulnerabilities)}"
                )

                panel_content = summary
                if dep_res.vulnerabilities:
                    from archguard.cli._analyze_core import _status
                    _status(table, opts)
                    if len(dep_res.vulnerabilities) > 5:
                        _status(
                            f"[dim]... and {len(dep_res.vulnerabilities) - 5} more vulnerabilities.[/dim]",
                            opts
                        )

                from archguard.cli._analyze_core import _status
                _status(
                    Panel(
                        panel_content,
                        title="Dependency Health Score",
                        border_style="cyan",
                    ),
                    opts
                )

        if caught_exit is not None:
            raise caught_exit

    except typer.Exit:
        raise
    except Exception as e:
        from rich.console import Console

        console = Console(stderr=True)
        console.print(f"[bold red]ArchGuard Error:[/bold red] {e}")
        if verbose:
            console.print_exception()
        else:
            console.print("[dim]Run with --verbose for full traceback[/dim]")
        raise typer.Exit(1)
