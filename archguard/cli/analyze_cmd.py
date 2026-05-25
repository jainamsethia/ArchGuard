"""archguard analyze — full architectural drift analysis."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from archguard.analysis.layers import AnalysisOrchestrator, AnalysisResult
from archguard.analysis.scoring import ArchDebtBand
from archguard.config import EXIT_OK, EXIT_VIOLATION
from archguard.utils.errors import ConfigError, format_error

analyze_app: typer.Typer = typer.Typer(
    name="analyze",
    help="Analyze the codebase for architectural drift.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

_console: Console = Console()

_BAND_EMOJI: dict[str, str] = {
    "Healthy": "✅ Healthy",
    "Watch": "⚠️ Watch",
    "Warn": "🔶 Warn",
    "Critical": "🚨 Critical",
}


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
            repo_root / f.strip()
            for f in changed_files_arg.split(",")
            if f.strip()
        ]

    if pr_number and repo_slug:
        try:
            from archguard.github.client import GitHubClient

            client = GitHubClient()
            files = client.get_pr_changed_files(repo_slug, pr_number)
            return [repo_root / f for f in files]
        except Exception:  # noqa: BLE001
            pass

    # Fallback: git diff
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD~1", "--name-only"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=30,
        )
        if result.returncode == 0:
            return [
                repo_root / f.strip()
                for f in result.stdout.strip().splitlines()
                if f.strip()
            ]
    except Exception:  # noqa: BLE001
        pass

    return []


def _print_rich_report(result: AnalysisResult, repo_root: Path) -> None:
    """Print Rich-formatted analysis report."""
    archdebt = result.archdebt
    band_str = _BAND_EMOJI.get(archdebt.band.value, archdebt.band.value)
    ci_str = "CI PASSED" if not archdebt.should_fail_ci else "CI FAILED"

    _console.print()
    _console.print("[bold]ArchGuard Analysis[/bold]")
    _console.print("─" * 38)
    _console.print(f"Repo:    {repo_root}")
    _console.print(f"Commit:  {result.commit_sha}")
    _console.print(f"Files:   {len(result.changed_files)} changed Python files")
    _console.print(f"ArchDebt Score:  {archdebt.composite_score:.2f}   {band_str}")
    _console.print()
    _console.print("[bold]Layer Scores:[/bold]")
    _console.print(f"  L1 Import:      {archdebt.layer_scores.layer1_violation:.2f}")
    _console.print(f"  L2 Coupling:    {archdebt.layer_scores.layer2_coupling:.2f}")
    _console.print(f"  L3 Drift:       {archdebt.layer_scores.layer3_drift:.2f}")
    _console.print(f"  L4 Duplication: {archdebt.layer_scores.layer4_duplication:.2f}")

    if result.violations:
        _console.print()
        _console.print(f"[bold]Violations ({len(result.violations)}):[/bold]")
        for v in result.violations:
            _console.print(
                f"  [L{v.layer}] {v.module}: {v.message} — {v.commit_sha[:7]}"
            )

    _console.print()
    color = "green" if ci_str == "CI PASSED" else "red"
    _console.print(f"[bold {color}]Result: {ci_str}[/bold {color}]")


def _build_json_output(result: AnalysisResult) -> dict[str, Any]:
    """Build JSON-serializable dict from AnalysisResult."""
    return {
        "composite_score": result.archdebt.composite_score,
        "band": result.archdebt.band.value,
        "should_fail_ci": result.archdebt.should_fail_ci,
        "layer_scores": {
            "layer1": result.archdebt.layer_scores.layer1_violation,
            "layer2": result.archdebt.layer_scores.layer2_coupling,
            "layer3": result.archdebt.layer_scores.layer3_drift,
            "layer4": result.archdebt.layer_scores.layer4_duplication,
        },
        "violations": [
            {
                "layer": v.layer,
                "module": v.module,
                "message": v.message,
                "commit_sha": v.commit_sha,
                "file_path": v.file_path,
            }
            for v in result.violations
        ],
        "commit_sha": result.commit_sha,
        "changed_files": result.changed_files,
    }


@analyze_app.callback(invoke_without_command=True)
def analyze_command(
    repo: Path = typer.Option(
        Path("."), "--repo", help="Path to the repository root.",
    ),
    pr: int | None = typer.Option(
        None, "--pr", help="Pull request number.",
    ),
    repo_slug: str | None = typer.Option(
        None, "--repo-slug", help="Repository slug (e.g. myorg/myrepo).",
    ),
    changed_files: str | None = typer.Option(
        None, "--changed-files",
        help="Comma-separated file list or @filename.",
    ),
    skip_explanation: bool = typer.Option(
        False, "--skip-explanation", help="Skip Layer 4 LLM explanation.",
    ),
    full: bool = typer.Option(
        False, "--full", help="Force full corpus rebuild.",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output JSON instead of Rich table.",
    ),
    fail_on_warn: bool = typer.Option(
        False, "--fail-on-warn", help="Exit 1 on Watch band too.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Analyze but don't post PR comment.",
    ),
) -> None:
    """Run architectural drift analysis."""
    repo_root = repo.resolve()

    # Auto-detect GitHub Actions env vars
    repo_slug = repo_slug or os.environ.get("GITHUB_REPOSITORY")
    if pr is None:
        pr_env = os.environ.get("GITHUB_PR_NUMBER", "0")
        try:
            pr_val = int(pr_env)
            if pr_val > 0:
                pr = pr_val
        except ValueError:
            pass

    # Load contract
    try:
        orchestrator = AnalysisOrchestrator(repo_root)
    except ConfigError as exc:
        _console.print(format_error(str(exc)))
        raise typer.Exit(EXIT_VIOLATION) from exc

    # Resolve changed files
    all_changed = _resolve_changed_files(
        repo_root, changed_files, pr, repo_slug,
    )
    py_changed = [f for f in all_changed if str(f).endswith(".py")]

    if not py_changed:
        _console.print("No Python files changed. Skipping analysis.")
        raise typer.Exit(EXIT_OK)

    # Get commit SHA
    commit_sha = AnalysisOrchestrator.get_commit_sha(repo_root)

    # Run analysis
    try:
        result = orchestrator.run(
            py_changed, commit_sha, skip_explanation=skip_explanation,
        )
    except Exception as exc:
        _console.print(format_error(f"Analysis failed: {exc}"))
        raise typer.Exit(2) from exc

    # Output
    if json_output:
        _console.print(json.dumps(_build_json_output(result), indent=2))
    else:
        _print_rich_report(result, repo_root)

    # Post PR comment (if applicable)
    if pr and repo_slug and not dry_run:
        try:
            from archguard.github.client import GitHubClient
            from archguard.github.comments import PRCommentManager

            client = GitHubClient()
            manager = PRCommentManager(client)
            body = manager.format_report(result)
            manager.post_or_update(repo_slug, pr, body)
        except Exception:  # noqa: BLE001
            _console.print("[yellow]Warning: Failed to post PR comment.[/yellow]")

    # Determine exit code
    should_fail = result.archdebt.should_fail_ci
    if fail_on_warn and result.archdebt.band in (
        ArchDebtBand.WATCH,
        ArchDebtBand.WARN,
    ):
        should_fail = True

    if should_fail:
        raise typer.Exit(EXIT_VIOLATION)
