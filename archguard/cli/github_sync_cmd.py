"""archguard github-sync - parse and execute GitHub PR commands."""

import json
import os
import typing
from pathlib import Path

import typer
from rich.console import Console

from archguard.config import EXIT_SUCCESS
from archguard.github.commands import ArchGuardCommand, parse_commands

github_sync_app = typer.Typer(
    name="github-sync",
    help="Sync and execute commands from GitHub issue_comment events.",
    no_args_is_help=False,
)

_console = Console()


@github_sync_app.callback(invoke_without_command=True)
def github_sync(
    repo: Path = typer.Option(Path("."), "--repo", help="Repository root."),
) -> None:
    """Read GITHUB_EVENT_PATH, parse slash commands, and execute them."""
    try:
        from archguard.config import EXIT_CONFIG_ERROR
        from archguard.utils.validation import PathTraversalError, validate_repo_path

        repo = validate_repo_path(repo)
    except PathTraversalError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR)

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        _console.print("GITHUB_EVENT_PATH not set. Skipping github-sync.")
        raise typer.Exit(EXIT_SUCCESS)

    try:
        with open(event_path, encoding="utf-8") as f:
            event = json.load(f)
    except Exception as exc:
        _console.print(f"Failed to read GITHUB_EVENT_PATH: {exc}")
        raise typer.Exit(EXIT_CONFIG_ERROR)

    action = event.get("action")
    if action != "created":
        _console.print(f"Ignoring issue_comment action: {action}")
        raise typer.Exit(EXIT_SUCCESS)

    comment = event.get("comment", {})
    body = comment.get("body", "")
    comment_id = comment.get("id", 0)
    author = comment.get("user", {}).get("login", "unknown")

    issue = event.get("issue", {})
    if "pull_request" not in issue:
        _console.print("Comment is not on a pull request. Skipping.")
        raise typer.Exit(EXIT_SUCCESS)

    pr_number = issue.get("number")
    repo_slug = event.get("repository", {}).get("full_name") or os.environ.get(
        "GITHUB_REPOSITORY"
    )

    if not body.strip().startswith("/archguard"):
        _console.print("No /archguard commands found at the start of the comment.")
        raise typer.Exit(EXIT_SUCCESS)

    commands = parse_commands(body, comment_id, author)
    if not commands:
        _console.print("No valid /archguard commands parsed.")
        raise typer.Exit(EXIT_SUCCESS)

    repo_root = repo.resolve()

    for cmd in commands:
        if cmd.command == ArchGuardCommand.SUPPRESS:
            _execute_suppress(cmd, repo_root, repo_slug or "", pr_number or 0)
        elif cmd.command == ArchGuardCommand.RE_ANALYZE:
            _execute_re_analyze(repo_root, repo_slug or "", pr_number or 0, None)
        else:
            _console.print(
                f"Command {cmd.command} is not implemented in github-sync yet."
            )


def _execute_suppress(
    cmd: typing.Any, repo_root: Path, repo_slug: str, pr_number: int
) -> None:
    from archguard.github.client import post_comment
    from archguard.suppression.store import SuppressionStore, SuppressionValidationError

    if len(cmd.args) < 3:
        usage_msg = (
            f"❌ @{cmd.author} Usage: "
            "`/archguard suppress <module> <layer> <message>`\n"
            "Example: `/archguard suppress api 1 Imports from db directly`"
        )
        if repo_slug and pr_number:
            post_comment(repo_slug, usage_msg, pr_number=pr_number)
        return

    module = cmd.args[0]
    try:
        layer = int(cmd.args[1])
    except ValueError:
        error_msg = (
            f"❌ @{cmd.author} Layer must be an integer (1–4). Got: {cmd.args[1]}"
        )
        if repo_slug and pr_number:
            post_comment(repo_slug, error_msg, pr_number=pr_number)
        return

    message = " ".join(cmd.args[2:])
    store = SuppressionStore(repo_root)

    try:
        store.add(
            module=module,
            layer=layer,
            message=message,
            reason=f"Suppressed via PR comment by @{cmd.author}",
            pr_number=pr_number,
        )
        success_msg = f"✅ @{cmd.author} Suppressed `{module}` L{layer}: `{message}`"
    except SuppressionValidationError as exc:
        success_msg = f"❌ @{cmd.author} Suppression failed: {exc}"
    except Exception as exc:
        success_msg = f"❌ @{cmd.author} Suppression failed: {exc}"

    if repo_slug and pr_number:
        post_comment(repo_slug, success_msg, pr_number=pr_number)


def _execute_re_analyze(
    repo_root: Path, repo_slug: str, pr_number: int, _ctx: object | None
) -> None:
    """Trigger re-analysis without requiring a Typer context.
    Calls AnalysisOrchestrator directly - never calls a Typer command.
    Typer commands require a live Typer context; the analysis engine does not.
    """
    import logging

    logger = logging.getLogger(__name__)

    try:
        from archguard.analysis.layers import AnalysisOrchestrator
        from archguard.cli._analyze_core import _resolve_changed_files
        from archguard.github.client import GitHubClient
        from archguard.github.comments import PRCommentManager

        commit_sha = AnalysisOrchestrator.get_commit_sha(repo_root)
        changed_files = _resolve_changed_files(repo_root, None, pr_number, repo_slug)

        with AnalysisOrchestrator(repo_root) as orchestrator:
            result = orchestrator.run(
                changed_files=changed_files, commit_sha=commit_sha, quiet=True
            )

        if result and repo_slug and pr_number:
            client = GitHubClient()
            manager = PRCommentManager(client)
            body = manager.format_report(result)

            risk_report = None
            try:
                from archguard.analysis._orchestrator_utils import _get_module_paths
                from archguard.fitness.evaluator import FitnessFunctionEvaluator
                from archguard.risk.pr_risk import PRRiskAnalyzer

                analyzer = PRRiskAnalyzer()
                module_paths = {
                    m["name"]: _get_module_paths(m)
                    for m in orchestrator.contract.get("modules", [])
                }
                changed_files_str = [
                    str(f.relative_to(repo_root)).replace("\\", "/")
                    for f in changed_files
                ]

                evaluator = FitnessFunctionEvaluator(repo_root, orchestrator.contract)
                dep_set = evaluator._get_module_dependencies()
                dependency_graph = {k: list(v) for k, v in dep_set.items()}

                risk_report = analyzer.analyze(
                    changed_files=changed_files_str,
                    module_paths=module_paths,
                    dependency_graph=dependency_graph,
                )
            except ImportError:
                pass

            if risk_report:
                from archguard.github.comments import build_risk_section

                risk_section = build_risk_section(risk_report)
                if risk_section:
                    body += "\n\n" + risk_section

            client.post_comment(repo_slug, body, pr_number=pr_number)

            if risk_report:
                from archguard.config import ArchGuardConfig

                config = ArchGuardConfig()
                if (
                    config.fail_on_critical_risk
                    and getattr(risk_report, "overall_risk", "").lower() == "critical"
                ):
                    raise typer.Exit(code=1)

    except typer.Exit:
        raise
    except Exception as exc:
        logger.exception("Re-analysis failed: %s", exc)
