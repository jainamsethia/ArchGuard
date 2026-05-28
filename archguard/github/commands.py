"""/archguard command parser for PR comments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from archguard.github.client import GitHubClient


class ArchGuardCommand(str, Enum):
    """Commands recognized in PR comments."""

    ACCEPT_CONTRACT = "accept-contract"
    REJECT_CONTRACT = "reject-contract"
    REINFER_CONTRACT = "reinfer-contract"
    SUPPRESS = "suppress"
    RE_ANALYZE = "re-analyze"


COMMAND_PATTERN: re.Pattern[str] = re.compile(
    r"^/archguard[ \t]+(accept-contract|reject-contract|reinfer-contract|suppress|re-analyze)"
    r"(?:[ \t]+(.*))?",
    re.MULTILINE | re.IGNORECASE,
)


@dataclass
class SlashCommand:
    """A parsed /archguard command from a PR comment."""

    command: ArchGuardCommand
    args: list[str]
    comment_id: int
    author: str


def parse_commands(
    comment_body: str,
    comment_id: int,
    author: str,
) -> list[SlashCommand]:
    """Parse all ``/archguard`` commands from a comment body."""
    results: list[SlashCommand] = []
    for match in COMMAND_PATTERN.finditer(comment_body):
        cmd_str = match.group(1).lower()
        args_str = match.group(2)
        args = args_str.split() if args_str else []
        try:
            cmd = ArchGuardCommand(cmd_str)
        except ValueError:
            continue
        results.append(SlashCommand(
            command=cmd,
            args=args,
            comment_id=comment_id,
            author=author,
        ))
    return results


def validate_command_access(
    client: GitHubClient,
    repo_slug: str,
    author: str,
) -> bool:
    """Return ``True`` if *author* is a collaborator on *repo_slug*."""
    return client.is_collaborator(repo_slug, author)
