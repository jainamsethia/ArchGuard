from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from enum import Enum
from rich.console import Console
from archguard.analysis.layers import AnalysisResult
from archguard.utils.errors import format_warning
from archguard.cli.analyze_cmd import AnalyzeOptions

_console = Console()
_BAND_EMOJI = {
    "Healthy": "[OK] Healthy",
    "Watch": "[!] Watch",
    "Warn": "🔶 Warn",
    "Critical": "[!] Critical",
}


def _print_rich_report(result: AnalysisResult, repo_root: Path) -> None:
    """Print Rich-formatted analysis report."""
    archdebt = result.archdebt
    band_str = _BAND_EMOJI.get(archdebt.band.value, archdebt.band.value)
    ci_str = "CI PASSED" if not archdebt.should_fail_ci else "CI FAILED"

    _console.print()
    _console.print("[bold]ArchGuard Analysis[/bold]")
    _console.print("-" * 38)
    _console.print(f"Repo:    {repo_root}")
    _console.print(f"Commit:  {result.commit_sha}")
    _console.print(f"Files:   {len(result.changed_files)} changed Python files")

    if getattr(result, "partial_analysis", False):
        failures = getattr(result, "parse_failures", [])
        _console.print(
            f"[bold yellow][!] Analysis Partial: {len(failures)} files could not be parsed[/bold yellow]"
        )

    _console.print()

    from rich.table import Table

    table = Table(title="ArchGuard Analysis Summary", show_header=True)
    table.add_column("Layer", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Score", justify="right")
    table.add_column("Details")

    skipped = getattr(result, "skipped_layers_names", [])
    v_counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for v in result.violations:
        v_counts[v.layer] = v_counts.get(v.layer, 0) + 1

    # L1
    s1 = archdebt.layer_scores.layer1_violation
    st1 = (
        "[yellow]SKIPPED[/yellow]"
        if "boundaries" in skipped
        else ("[red]FAIL[/red]" if s1 > 0.0 else "[green]PASS[/green]")
    )
    table.add_row("L1 Boundaries", st1, f"{s1:.2f}", f"{v_counts[1]} violations")

    # L2
    s2 = archdebt.layer_scores.layer2_coupling
    st2 = (
        "[yellow]SKIPPED[/yellow]"
        if "coupling" in skipped
        else ("[red]FAIL[/red]" if s2 > 0.0 else "[green]PASS[/green]")
    )
    table.add_row("L2 Coupling", st2, f"{s2:.2f}", f"{v_counts[2]} violations")

    # L3
    s3 = archdebt.layer_scores.layer3_drift
    st3 = (
        "[yellow]SKIPPED[/yellow]"
        if "semantic" in skipped
        else ("[red]FAIL[/red]" if s3 > 0.0 else "[green]PASS[/green]")
    )
    table.add_row("L3 Drift", st3, f"{s3:.2f}", f"{v_counts[3]} violations")

    # L4
    s4 = archdebt.layer_scores.layer4_duplication
    st4 = (
        "[yellow]SKIPPED[/yellow]"
        if "duplication" in skipped
        else ("[red]FAIL[/red]" if s4 > 0.0 else "[green]PASS[/green]")
    )
    table.add_row("L4 Duplication", st4, f"{s4:.2f}", f"{v_counts[4]} violations")

    _console.print(table)
    _console.print(
        f"\n[bold]Health Score: {archdebt.health_score:.1f} (Grade {archdebt.health_grade}) - {band_str}[/bold]"
    )
    _console.print(f"Result: {ci_str}\n")

    if result.violations:
        from rich.table import Table

        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        severity_colors = {
            "critical": "bold red",
            "high": "red",
            "medium": "yellow",
            "low": "dim white",
        }

        sorted_violations = sorted(
            result.violations,
            key=lambda v: (
                severity_order.get(getattr(v, "severity", "low"), 99),
                v.layer,
            ),
        )

        _console.print()
        _console.print(f"[bold]Violations ({len(result.violations)}):[/bold]")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Layer", style="dim", width=6)
        table.add_column("Severity", style="bold")
        table.add_column("Module", style="cyan")
        table.add_column("Message")

        for v in sorted_violations:
            sev_val = getattr(v, "severity", "low")
            sev_style = severity_colors.get(sev_val, "white")
            sev_str = f"[{sev_style}]{str(sev_val).upper()}[/{sev_style}]"
            explanation = getattr(v, "explanation", "")
            msg = f"{v.message} - {v.commit_sha[:7]}"
            if explanation:
                msg += f"\n[dim italic]AI: {explanation}[/dim italic]"
            table.add_row(
                f"L{v.layer}", sev_str, v.module, msg
            )

        _console.print(table)
        _console.print(
            "Legend: [bold red]CRITICAL[/bold red] = Layer breach [red]HIGH[/red] = Cycle/Coupling [yellow]MEDIUM[/yellow] = Duplication [dim white]LOW[/dim white] = Cohesion"
        )

    _console.print()
    color = "green" if ci_str == "CI PASSED" else "red"
    _console.print(f"[bold {color}]Result: {ci_str}[/bold {color}]")


def _build_json_report(
    score: float,
    grade: str,
    violations: list[dict[str, Any]],
    metrics: dict[str, float],
) -> dict[str, Any]:
    """Build JSON-serializable dict conforming to the analysis report schema."""
    import datetime

    total_violations = len(violations)
    suppressed_violations = sum(1 for v in violations if v.get("suppressed", False))
    active_violations = total_violations - suppressed_violations

    return {
        "score": score,
        "grade": grade,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "violations": violations,
        "metrics": metrics,
        "summary": {
            "total_violations": total_violations,
            "suppressed_violations": suppressed_violations,
            "active_violations": active_violations,
        },
    }


def _format_rich_output(result: AnalysisResult, opts: AnalyzeOptions) -> None:
    if opts.ctx.obj.get("quiet"):
        ci_str = "PASSED" if not result.archdebt.should_fail_ci else "FAILED"
        _console.print(
            f"Health Score: {result.archdebt.health_score:.1f} (Grade {result.archdebt.health_grade}) | CI: {ci_str}"
        )
    else:
        _print_rich_report(result, opts.repo.resolve())


def _write_json_output(result: AnalysisResult, opts: AnalyzeOptions) -> None:
    v_list_out = []
    for v in result.violations:
        sev = getattr(v, "severity", "low")
        v_list_out.append(
            {
                "type": "layer",
                "layer": getattr(v, "layer", 0),
                "file": str(getattr(v, "file_path", getattr(v, "module", ""))),
                "message": getattr(v, "message", ""),
                "severity": sev.value if isinstance(sev, Enum) else str(sev),
                "suppressed": getattr(v, "suppressed", False),
                "explanation": getattr(v, "explanation", ""),
            }
        )
    if opts.out_file is not None:
        band_val = str(result.archdebt.band.name).upper()
        out_band = (
            "PASS"
            if band_val in ("HEALTHY", "WATCH")
            else ("WARN" if band_val == "WARN" else "FAIL")
        )
        result_dict = {
            # composite_score: 0.0–1.0, HIGHER = WORSE (debt score)
            # health_score: 0–100, HIGHER = BETTER (use for user display)
            "score": result.archdebt.health_score,
            "health_grade": result.archdebt.health_grade,
            "band": out_band,
            "violations": v_list_out,
            "layer_results": {
                "layer1_violation": float(result.archdebt.layer_scores.layer1_violation)
                * 100,
                "layer2_coupling": float(result.archdebt.layer_scores.layer2_coupling)
                * 100,
                "layer3_drift": float(result.archdebt.layer_scores.layer3_drift) * 100,
                "layer4_duplication": float(
                    result.archdebt.layer_scores.layer4_duplication
                )
                * 100,
            },
            "fail_fast_triggered": getattr(result, "fail_fast_triggered", False),
        }
        if getattr(result, "fail_fast_triggered", False):
            result_dict["skipped_layers"] = [
                {"status": "skipped", "reason": "fail-fast", "layer": layer}
                for layer in getattr(result, "skipped_layers_names", [])
            ]
        opts.out_file.parent.mkdir(parents=True, exist_ok=True)
        opts.out_file.write_text(json.dumps(result_dict, indent=2, default=str))
    if opts.json_output:
        # composite_score: 0.0–1.0, HIGHER = WORSE (debt score)
        # health_score: 0–100, HIGHER = BETTER (use for user display)
        score = result.archdebt.health_score
        grade = str(result.archdebt.band.value)
        metrics = {
            "layer_score": float(result.archdebt.layer_scores.layer1_violation) * 100,
            "coupling_score": float(result.archdebt.layer_scores.layer2_coupling) * 100,
            "duplication_score": float(result.archdebt.layer_scores.layer4_duplication)
            * 100,
            "semantic_score": float(result.archdebt.layer_scores.layer3_drift) * 100,
        }
        report = _build_json_report(score, grade, v_list_out, metrics)
        report["fail_fast_triggered"] = getattr(result, "fail_fast_triggered", False)
        if getattr(result, "fail_fast_triggered", False):
            report["skipped_layers"] = [
                {"status": "skipped", "reason": "fail-fast", "layer": layer}
                for layer in getattr(result, "skipped_layers_names", [])
            ]
        import typer

        typer.echo(json.dumps(report, indent=2))


def _write_audit_log(result: AnalysisResult, opts: AnalyzeOptions) -> None:
    try:
        from archguard.audit.logger import AuditLogger
        from archguard.config import AUDIT_EVENT_ANALYSIS

        repo_root = opts.repo.resolve()
        audit = AuditLogger(log_path=repo_root / ".archguard-cache" / "audit.jsonl")
        band_val = str(result.archdebt.band.name).upper()
        audit_band = (
            "PASS"
            if band_val in ("HEALTHY", "WATCH")
            else ("WARN" if band_val == "WARN" else "FAIL")
        )
        from archguard.dashboard._result_schema import AnalysisResultPayload, ViolationPayload
        v_list_out = []
        for v in result.violations:
            sev = getattr(v, "severity", "low")
            v_list_out.append(
                ViolationPayload(
                    file=getattr(v, "file_path", "") or None,
                    module=getattr(v, "module_name", None),
                    severity=sev.value if isinstance(sev, Enum) else str(sev),
                    message=getattr(v, "message", ""),
                    layer=str(getattr(v, "layer", "0")),
                )
            )

        payload = AnalysisResultPayload(
            job_id="cli_run",
            score=result.archdebt.health_score,
            band=audit_band,
            violations=v_list_out,
            skipped=False
        )

        audit.log(
            AUDIT_EVENT_ANALYSIS,
            **payload.model_dump()
        )
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(f"Failed to log analysis_complete: {e}")


def _send_slack_alerts(result: AnalysisResult, repo_root: Path) -> None:
    import os

    slack_webhook = os.getenv("ARCHGUARD_SLACK_WEBHOOK")
    if slack_webhook:
        try:
            from archguard.utils.async_utils import run_async
            from archguard.alerting.trend_detector import detect_trends
            from archguard.alerting.webhooks import send_slack_alert
            from archguard.audit.logger import AuditLogger

            audit_logger = AuditLogger(
                log_path=repo_root / ".archguard-cache" / "audit.jsonl"
            )
            runs = audit_logger.read_last_n_runs(n=10)
            alerts = detect_trends(runs, window=10)
            if alerts:
                run_async(send_slack_alert(slack_webhook, alerts))
        except ValueError as ve:
            _console.print(format_warning(f"Invalid Slack webhook URL: {ve}"))
        except Exception as e:
            _console.print(format_warning(f"Failed to send Slack alert: {e}"))


def _show_monorepo_summary(results: list[tuple[str, AnalysisResult]]) -> None:
    from rich.table import Table

    table = Table(title="Monorepo Analysis Summary")
    table.add_column("Package")
    table.add_column("ArchDebt")
    table.add_column("Band")
    table.add_column("Violations")

    for name, result in results:
        table.add_row(
            name,
            f"{result.archdebt.health_score:.1f} ({result.archdebt.health_grade})",
            result.archdebt.band.value,
            str(len(result.violations)),
        )
    _console.print(table)

    if results:
        avg_health = sum(r.archdebt.health_score for _, r in results) / len(results)
        _console.print(f"\nMonorepo Health Score: {avg_health:.1f}")
