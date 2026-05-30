"""archguard github-sync — parse and execute GitHub PR commands."""

import json
import os
import typing
from pathlib import Path

import typer
from rich.console import Console

from archguard.config import EXIT_SUCCESS
from archguard.github.commands import parse_commands, ArchGuardCommand

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
        from archguard.utils.validation import validate_repo_path, PathTraversalError
        from archguard.config import EXIT_CONFIG_ERROR

        repo = validate_repo_path(repo)
    except PathTraversalError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR)

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        _console.print("GITHUB_EVENT_PATH not set. Skipping github-sync.")
        raise typer.Exit(EXIT_SUCCESS)

    try:
        with open(event_path, "r", encoding="utf-8") as f:
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
            _execute_re_analyze(repo_root, repo_slug or "", pr_number or 0, None)  # type: ignore[arg-type]
        else:
            _console.print(
                f"Command {cmd.command} is not implemented in github-sync yet."
            )


def _execute_suppress(
    cmd: typing.Any, repo_root: Path, repo_slug: str, pr_number: int
) -> None:
    from archguard.suppression.store import SuppressionStore, SuppressionValidationError
    from archguard.github.client import post_comment

    store = SuppressionStore(repo_root)
    violation_str = " ".join(cmd.args)

    if not violation_str:
        return

    try:
        store.add(
            module="unknown",
            layer=1,
            message=violation_str,
            reason=f"Suppressed via PR comment by {cmd.author}",
            pr_number=pr_number,
        )
        msg = f"✅ @{cmd.author} Suppressed violation: `{violation_str}`"
    except SuppressionValidationError as exc:
        msg = f"❌ @{cmd.author} Failed to suppress violation: {exc}"

    if repo_slug and pr_number:
        post_comment(repo_slug, msg, pr_number=pr_number)


def _execute_re_analyze(
    repo_root: Path, repo_slug: str, pr_number: int, ctx: typer.Context
) -> None:
    from archguard.cli.analyze_cmd import analyze_command

    try:
        analyze_command(
            ctx=ctx,
            repo=repo_root,
            pr=pr_number,
            repo_slug=repo_slug,
            json_output=False,
            dry_run=False,
        )
    except typer.Exit:
        pass
