import typer
from pathlib import Path
from rich.console import Console
from archguard.cli.analyze_cmd import AnalyzeOptions

def _run_analyze_cli(
    opts: AnalyzeOptions,
    json_output: bool,
    watch: bool,
    monorepo: bool,
    verbose: bool,
    repo: Path,
    pr: int | None,
    repo_slug: str | None,
    profile: str | None,
    changed_files: str | None,
    skip_explanation: bool,
    full: bool,
    fail_on_warn: bool,
    dry_run: bool,
    incremental: bool,
    no_incremental: bool,
    no_llm: bool,
    out_file: Path | None,
    fail_fast: bool,
    fail_threshold: float | None = None,
) -> None:
    from archguard.cli._analyze_core import _analyze_command_impl
    from archguard.cli._analyze_output import _show_monorepo_summary
    
    if json_output:
        opts.ctx.ensure_object(dict)
        opts.ctx.obj["quiet"] = True

    if watch:
        from archguard.cli.watch_cmd import run_watch_mode
        run_watch_mode(opts, Path(repo))
    elif monorepo:
        from archguard.utils.monorepo import detect_subpackages
        _console = Console()
        packages = detect_subpackages(Path(repo))
        if not packages:
            typer.echo("No sub-packages found. Run without --monorepo.")
            raise typer.Exit(1)
        results = []
        has_error = False
        for pkg in packages:
            pkg_contract = pkg / ".archguard.yml"
            if not pkg_contract.exists():
                _console.print(f"[yellow]No .archguard.yml in {pkg.name}, skipping[/yellow]")
                continue
            pkg_opts = AnalyzeOptions(
                ctx=opts.ctx,
                repo=pkg,
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
                verbose=verbose,
            )
            exit_code, result = _analyze_command_impl(pkg_opts)
            if exit_code not in (0, 1) or result is None:
                has_error = True
            if result:
                results.append((pkg.name, result))

        _show_monorepo_summary(results)
        if has_error:
            raise typer.Exit(2)
        if any(r.archdebt.should_fail_ci for _, r in results):
            raise typer.Exit(1)
    else:
        exit_code, result = _analyze_command_impl(opts)
        if exit_code != 0:
            raise typer.Exit(exit_code)
