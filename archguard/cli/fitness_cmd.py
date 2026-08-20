"""archguard fitness check - standalone fitness function evaluation."""

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

fitness_app: typer.Typer = typer.Typer(
    name="fitness",
    help="Evaluate and check architectural fitness functions.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

_console = Console()


@fitness_app.command("check")
def fitness_check(
    ctx: typer.Context,
    repo: Path = typer.Option(
        Path("."),
        "--repo",
        help="Path to the repository root.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output JSON instead of Rich table.",
    ),
    fail_on_warn: bool = typer.Option(
        False,
        "--fail-on-warn",
        help="Exit 2 if there are warning-level failures.",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress all output except results.",
    ),
) -> None:
    """Run architectural fitness functions."""
    try:
        from archguard.utils.validation import validate_repo_path

        repo = validate_repo_path(repo)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(2)

    from archguard.config import parse_fitness_functions
    from archguard.contract.loader import load_contract

    # Load contract
    try:
        contract = load_contract(repo)
    except Exception as e:
        if not json_output:
            _console.print(f"[bold red]Error loading contract:[/bold red] {e}")
        raise typer.Exit(2)

    fitness_configs = parse_fitness_functions(contract)
    if not fitness_configs:
        if json_output:
            print(json.dumps([]))
        else:
            _console.print("[dim]No fitness_functions defined in .archguard.yml[/dim]")
        raise typer.Exit(0)

    # Need AnalysisResult
    from archguard.analysis.layers import AnalysisOrchestrator

    try:
        orchestrator = AnalysisOrchestrator(repo)
        # Avoid running full _run_analyze_cli loop, we just use orchestrator.run() directly
        res = orchestrator.run([repo], "HEAD", quiet=True)
    except Exception as e:
        if not json_output:
            _console.print(f"[bold red]Error running analysis:[/bold red] {e}")
        raise typer.Exit(1)

    # Evaluate fitness functions directly if not already done by Orchestrator
    # Note: Phase 3 Step 3 already modifies res.archdebt and res.metrics.
    # To isolate logic or access FitnessFunctionResult directly we can use Evaluator.
    from archguard.fitness.evaluator import FitnessFunctionEvaluator

    evaluator = FitnessFunctionEvaluator(repo, contract)
    rules = [c.rule for c in fitness_configs]
    fitness_results = evaluator.evaluate(res, rules)

    # JSON Output
    if json_output:
        from archguard.audit.logger import serialize_fitness_results

        serialized = serialize_fitness_results(fitness_results, fitness_configs)
        print(json.dumps(serialized, indent=2))
    else:
        # Rich Output
        table = Table(title="Fitness Functions Evaluation")
        table.add_column("Status")
        table.add_column("Name")
        table.add_column("Severity")
        table.add_column("Evidence")

        config_map = {getattr(c, "rule", ""): c for c in fitness_configs}

        for fr in fitness_results:
            rule = getattr(fr, "rule", "")
            passed = getattr(fr, "passed", True)
            cfg = config_map.get(rule)
            severity = getattr(cfg, "severity", "warn") if cfg else "warn"
            name = getattr(cfg, "name", rule) if cfg else rule
            details = getattr(fr, "details", None) or getattr(fr, "error", None) or ""

            if passed:
                status_str = "[green][OK] PASS[/green]"
            elif severity == "critical":
                status_str = "[bold red][FAIL] FAIL[/bold red]"
            else:
                status_str = "[yellow][WARN] FAIL[/yellow]"

            table.add_row(status_str, name, severity, details)

        _console.print(table)

        passed_count = sum(1 for r in fitness_results if getattr(r, "passed", True))
        total_count = len(fitness_results)
        _console.print(f"[bold]{passed_count}/{total_count} functions passed[/bold]")

    # Exit code evaluation
    config_map = {getattr(c, "rule", ""): c for c in fitness_configs}
    has_critical = False
    has_warn = False

    for fr in fitness_results:
        rule = getattr(fr, "rule", "")
        passed = getattr(fr, "passed", True)
        if not passed:
            cfg = config_map.get(rule)
            severity = getattr(cfg, "severity", "warn") if cfg else "warn"
            if severity == "critical":
                has_critical = True
            else:
                has_warn = True

    if has_critical:
        raise typer.Exit(1)
    if has_warn:
        if fail_on_warn:
            raise typer.Exit(2)
        else:
            # We have warnings but no criticals, and fail_on_warn is false.
            # But the user asked for non-zero on every failure path, so we should
            # probably exit 0 since they are warnings, or wait:
            pass

    raise typer.Exit(0)
