from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Any
from rich.console import Console
from archguard.analysis.layers import AnalysisOrchestrator, AnalysisResult
from archguard.analysis.scoring import ArchDebtBand
from archguard.config import (
    EXIT_SUCCESS,
    EXIT_VIOLATION,
    EXIT_CONFIG_ERROR,
    EXIT_ANALYSIS_ERROR,
)
from archguard.utils.errors import format_error
from archguard.utils.output import vprint
from archguard.profiles.defaults import apply_profile
from archguard.cli.analyze_cmd import AnalyzeOptions

from archguard.cli._analyze_output import (
    _format_rich_output,
    _write_json_output,
    _write_audit_log,
    _send_slack_alerts,
)
from archguard.cli._analyze_github import _post_github_annotations

_console = Console()


def attach_explanations(
    result: AnalysisResult,
    explanations: list[str],
) -> AnalysisResult:
    """Return a new AnalysisResult with explanations attached to violations."""
    from dataclasses import replace as dc_replace

    new_violations = []
    for i, v in enumerate(result.violations):
        explanation = explanations[i] if i < len(explanations) else ""
        new_violations.append(dc_replace(v, explanation=explanation))

    return dc_replace(result, violations=new_violations)


def _resolve_changed_files(
    repo_root: Path,
    changed_files_arg: str | None,
    pr_number: int | None,
    repo_slug: str | None,
) -> list[Path]:
    """Resolve changed files from CLI args, GitHub, or git diff."""
    if changed_files_arg:
        if changed_files_arg.startswith("@"):
            # Read from file
            list_file = Path(changed_files_arg[1:])
            if list_file.is_file():
                raw = list_file.read_text(encoding="utf-8").strip()
                return [repo_root / f.strip() for f in raw.splitlines() if f.strip()]
        # Comma-separated
        return [
            repo_root / f.strip() for f in changed_files_arg.split(",") if f.strip()
        ]

    if pr_number and repo_slug:
        try:
            from archguard.github.client import GitHubClient

            client = GitHubClient()
            files = client.get_pr_changed_files(repo_slug, pr_number)
            return [repo_root / f for f in files]
        except Exception as e:
            import logging

            logging.getLogger(__name__).error(
                f"Analysis failed in _resolve_changed_files: {e}", exc_info=True
            )
            raise

    # Fallback: git diff HEAD~1
    try:
        # First, check if HEAD~1 exists
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD~1"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            # HEAD~1 exists, use normal diff
            diff_cmd = ["git", "diff", "HEAD~1", "--name-only", "--diff-filter=ACMR"]
        else:
            # Initial commit or detached HEAD — diff against empty tree
            # The empty tree hash is a git constant, always valid
            EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
            diff_cmd = [
                "git",
                "diff",
                EMPTY_TREE,
                "HEAD",
                "--name-only",
                "--diff-filter=ACMR",
            ]
            _console.print(
                "[yellow]⚠ Initial commit detected — analyzing all Python files.[/yellow]"
            )

        diff_result = subprocess.run(
            diff_cmd, cwd=repo_root, capture_output=True, text=True
        )

        if diff_result.returncode != 0:
            # Log a warning, don't silently return empty
            import logging

            logging.warning(
                f"git diff failed: {diff_result.stderr}. Analyzing all Python files."
            )
            return list(repo_root.rglob("*.py"))

        diff_files = [
            Path(f)
            for f in diff_result.stdout.strip().splitlines()
            if f.endswith(".py")
        ]
        return [repo_root / f for f in diff_files if (repo_root / f).exists()]
    except Exception as e:
        import logging

        logging.getLogger(__name__).error(
            f"Analysis failed in _resolve_changed_files git fallback: {e}",
            exc_info=True,
        )
        raise

    return []


def _merge_incremental_results(
    result: AnalysisResult,
    repo_root: Path,
    unchanged: list[Path],
    commit_sha: str,
    contract: dict[str, Any],
    opts: AnalyzeOptions,
) -> None:
    from archguard.audit.logger import AuditLogger
    from archguard.analysis.layers import ViolationDetail
    from archguard.analysis.scoring import compute_archdebt, LayerScores

    last_run = AuditLogger(
        log_path=repo_root / ".archguard-cache" / "audit.jsonl"
    ).read_last_run()
    if last_run:
        unchanged_rel = {
            str(f.relative_to(repo_root)).replace("\\", "/") for f in unchanged
        }
        for v in last_run.get("violations", []):
            if v.get("file") in unchanged_rel:
                result.violations.append(
                    ViolationDetail(
                        layer=v.get("layer", 0),
                        module=v.get("module", ""),
                        message=v.get("message", ""),
                        commit_sha=commit_sha[:7],
                        file_path=v.get("file", ""),
                        explanation=v.get("explanation", ""),
                    )
                )
        metrics = last_run.get("metrics", {})
        if metrics:
            scores = LayerScores(
                layer1_violation=metrics.get("layer_score", 0) / 100.0,
                layer2_coupling=metrics.get("coupling_score", 0) / 100.0,
                layer3_drift=metrics.get("semantic_score", 0) / 100.0,
                layer4_duplication=metrics.get("duplication_score", 0) / 100.0,
            )
            weights_cfg = contract.get("weights", {})
            weights = (
                float(weights_cfg.get("layer1", 0.25)),
                float(weights_cfg.get("layer2", 0.25)),
                float(weights_cfg.get("layer3", 0.25)),
                float(weights_cfg.get("layer4", 0.25)),
            )
            result.layer_scores = scores
            result.archdebt = compute_archdebt(
                scores,
                weights=weights,
                fail_threshold=opts.fail_threshold
                if opts.fail_threshold is not None
                else float(contract.get("fail_threshold", 0.75)),
                warn_threshold=float(contract.get("warn_threshold", 0.50)),
            )
        result.changed_files.extend(list(unchanged_rel))


def _save_incremental_cache(
    repo_root: Path, py_changed: list[Path], unchanged: list[Path]
) -> None:
    from archguard.cache.incremental import (
        save_cache,
        load_cache,
        FileRecord,
        compute_hash,
    )
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    cache_records = load_cache(repo_root)
    for f in py_changed + unchanged:
        rel = str(f.relative_to(repo_root)).replace("\\", "/")
        cache_records[rel] = FileRecord(
            path=rel, sha256=compute_hash(f), last_analyzed=now
        )
    save_cache(repo_root, cache_records)

def _analyze_command_impl(opts: AnalyzeOptions) -> tuple[int, AnalysisResult | None]:
    import os

    SKIP_LLM = os.getenv("ARCHGUARD_SKIP_LLM", "").lower() in ("1", "true", "yes")
    if opts.no_llm or SKIP_LLM:
        opts.skip_explanation = True
    repo_root = opts.repo.resolve()

    opts.repo_slug = opts.repo_slug or os.environ.get("GITHUB_REPOSITORY")
    if opts.pr_number is None:
        from archguard.github.client import _get_pr_number

        opts.pr_number = _get_pr_number()

    try:
        orchestrator = AnalysisOrchestrator(repo_root)
    except Exception as e:
        _console.print(format_error(f"Failed to load contract: {e}"))
        return EXIT_CONFIG_ERROR, None

    with orchestrator:
        profile_to_use = opts.profile or orchestrator.contract.get("profile")
        if profile_to_use:
            orchestrator.contract = apply_profile(orchestrator.contract, profile_to_use)
            vprint(
                f"Applied configuration profile: [bold cyan]{profile_to_use}[/bold cyan]",
                opts.ctx,
            )

        all_changed = _resolve_changed_files(
            repo_root, opts.changed_files, opts.pr_number, opts.repo_slug
        )
        vprint(
            f"[bold blue]Analyzing {len(all_changed)} changed file(s)[/bold blue]",
            opts.ctx,
        )
        py_changed = [f for f in all_changed if str(f).endswith(".py")]

        unchanged: list[Path] = []
        if opts.incremental and not opts.no_incremental:
            from archguard.cache.incremental import get_changed_files

            py_changed, unchanged = get_changed_files(py_changed, repo_root)

        if not py_changed and not unchanged:
            vprint("No Python files changed. Skipping analysis.", opts.ctx)
            return EXIT_SUCCESS, None

        commit_sha = AnalysisOrchestrator.get_commit_sha(repo_root)

        try:
            vprint(
                f"Analyzing {len(py_changed)} changed files...", opts.ctx, level="debug"
            )
            result = orchestrator.run(
                py_changed,
                commit_sha,
                skip_explanation=opts.skip_explanation,
                progress_callback=None,
                fail_fast=opts.fail_fast,
                quiet=opts.json_output,
            )

            if opts.verbose or opts.metrics_flag:
                from rich.table import Table

                table = Table(title="Performance Metrics")
                table.add_column("Layer", style="cyan")
                table.add_column("Duration", justify="right", style="magenta")
                for layer, duration in result.metrics.get(
                    "layer_durations", {}
                ).items():
                    table.add_row(layer, f"{duration:.3f}s")
                _console.print(table)

            vprint("Analysis core completed.", opts.ctx, level="debug")

            if opts.incremental and not opts.no_incremental and unchanged:
                _merge_incremental_results(
                    result,
                    repo_root,
                    unchanged,
                    commit_sha,
                    orchestrator.contract,
                    opts,
                )
            if opts.incremental and not opts.no_incremental:
                _save_incremental_cache(repo_root, py_changed, unchanged)

        except RuntimeError as exc:
            if "ML dependencies" in str(exc):
                _console.print(
                    "\n[bold red]Missing Dependencies[/bold red]\nLayer 3 requires ML libraries."
                )
                return EXIT_CONFIG_ERROR, None
            _console.print(format_error(f"Analysis failed: {exc}"))
            return EXIT_ANALYSIS_ERROR, None
        except Exception as exc:
            _console.print(format_error(f"Analysis failed: {exc}"))
            return EXIT_ANALYSIS_ERROR, None

        from archguard.cli._analyze_llm import _run_llm_explanation
        result = _run_llm_explanation(result, orchestrator.contract, opts)

        if opts.json_output or opts.out_file is not None:
            _write_json_output(result, opts)
        if not opts.json_output:
            _format_rich_output(result, opts)

        _write_audit_log(result, opts)
        _post_github_annotations(result, opts, orchestrator.contract)

        if opts.pr_number and py_changed:
            from archguard.cli._analyze_risk import _check_pr_risk

            exit_code, result = _check_pr_risk(
                result, opts, orchestrator, py_changed, repo_root, EXIT_VIOLATION
            )
            if exit_code != 0:
                return exit_code, result

        has_critical_parse_failures = any(
            f.is_critical for f in getattr(result, "parse_failures", [])
        )
        if has_critical_parse_failures:
            if not opts.ctx.obj.get("quiet"):
                _console.print(
                    "[bold red]Critical parse failures detected. Analysis is invalid.[/bold red]"
                )
            return 3, result

        should_fail = result.archdebt.should_fail_ci
        if opts.fail_on_warn and result.archdebt.band in (
            ArchDebtBand.WATCH,
            ArchDebtBand.WARN,
        ):
            should_fail = True

        _send_slack_alerts(result, repo_root)

        if should_fail:
            return EXIT_VIOLATION, result
        return EXIT_SUCCESS, result
