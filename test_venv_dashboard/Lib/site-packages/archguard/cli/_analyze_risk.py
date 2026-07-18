from typing import Any
from pathlib import Path

from archguard.cli._analyze_output import _console
from archguard.config import ArchGuardConfig

# We assume AnalysisResult is imported dynamically or we can just annotate it
# but let's just make it work correctly without circular imports
def _check_pr_risk(
    result: Any,
    opts: Any,
    orchestrator: Any,
    py_changed: list[Path],
    repo_root: Path,
    EXIT_VIOLATION: int,
) -> tuple[int, Any]:
    from archguard.risk.pr_risk import PRRiskAnalyzer
    from archguard.fitness.evaluator import FitnessFunctionEvaluator
    from archguard.analysis._orchestrator_utils import _get_module_paths

    analyzer = PRRiskAnalyzer()
    module_paths = {
        m["name"]: _get_module_paths(m)
        for m in orchestrator.contract.get("modules", [])
    }
    changed_files_str = [
        str(f.relative_to(repo_root)).replace("\\", "/") for f in py_changed
    ]
    try:
        evaluator = FitnessFunctionEvaluator(repo_root, orchestrator.contract)
        dep_set = evaluator._get_module_dependencies()
        dependency_graph = {k: list(v) for k, v in dep_set.items()}

        risk_report = analyzer.analyze(
            changed_files=changed_files_str,
            module_paths=module_paths,
            dependency_graph=dependency_graph,
        )
        if not opts.json_output and not opts.ctx.obj.get("quiet"):
            from rich.table import Table

            rtable = Table(
                title=f"PR Risk Analysis (Score: {risk_report.risk_score})"
            )
            rtable.add_column("Module", style="cyan")
            rtable.add_column("Risk Level", style="magenta")
            for mr in risk_report.module_risks:
                rtable.add_row(mr.module, mr.risk_level)
            _console.print(rtable)
            _console.print(
                f"Overall Risk: [bold]{risk_report.overall_risk.upper()}[/bold]"
            )

        cfg = ArchGuardConfig()
        if (
            cfg.fail_on_critical_risk
            and risk_report.overall_risk.lower() == "critical"
        ):
            if not opts.ctx.obj.get("quiet"):
                _console.print(
                    "[bold red]Failing build due to CRITICAL PR risk.[/bold red]"
                )
            return EXIT_VIOLATION, result
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning(f"PR Risk Analysis failed: {exc}")
    return 0, result
