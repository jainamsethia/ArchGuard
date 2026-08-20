from dataclasses import replace
from pathlib import Path

import typer
from rich.console import Console

from archguard.cli.analyze_cmd import AnalyzeOptions


def _run_analyze_cli(opts: AnalyzeOptions) -> None:
    """Dispatch to watch mode, monorepo mode, or a single analysis run."""
    from archguard.cli._analyze_core import _analyze_command_impl
    from archguard.cli._analyze_output import _show_monorepo_summary

    if opts.json_output:
        opts.ctx.ensure_object(dict)
        opts.ctx.obj["quiet"] = True

    if opts.watch:
        from archguard.cli._analyze_watch import run_watch_mode

        run_watch_mode(opts, Path(opts.repo))
        return

    if not opts.monorepo:
        exit_code, _result = _analyze_command_impl(opts)
        if exit_code != 0:
            raise typer.Exit(exit_code)
        return

    from archguard.utils.monorepo import detect_subpackages

    console = Console()
    packages = detect_subpackages(Path(opts.repo))
    if not packages:
        typer.echo("No sub-packages found. Run without --monorepo.")
        raise typer.Exit(1)

    results = []
    has_error = False
    for pkg in packages:
        if not (pkg / ".archguard.yml").exists():
            console.print(f"[yellow]No .archguard.yml in {pkg.name}, skipping[/yellow]")
            continue
        # Every option carries over unchanged except the repo being analysed.
        # This used to re-list twenty constructor arguments by hand, which is
        # how --metrics, --watch, --monorepo and --fail-threshold silently
        # stopped applying to sub-packages.
        exit_code, result = _analyze_command_impl(
            replace(opts, repo=pkg, monorepo=False, watch=False)
        )
        if exit_code not in (0, 1) or result is None:
            has_error = True
        if result:
            results.append((pkg.name, result))

    _show_monorepo_summary(results)
    if has_error:
        raise typer.Exit(2)
    if any(r.archdebt.should_fail_ci for _, r in results):
        raise typer.Exit(1)
