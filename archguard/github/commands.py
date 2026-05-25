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


COMMAND_PATTERN: re.Pattern[str] = re.compile(
    r"^/archguard\s+(accept-contract|reject-contract|reinfer-contract)"
    r"(?:\s+(\S+))?",
    re.MULTILINE | re.IGNORECASE,
)


@dataclass
class ParsedCommand:
    """A parsed /archguard command from a PR comment."""

    command: ArchGuardCommand
    module_name: str | None
    comment_id: int
    author: str


def parse_commands(
    comment_body: str,
    comment_id: int,
    author: str,
) -> list[ParsedCommand]:
    """Parse all ``/archguard`` commands from a comment body."""
    results: list[ParsedCommand] = []
    for match in COMMAND_PATTERN.finditer(comment_body):
        cmd_str = match.group(1).lower()
        module = match.group(2)
        try:
            cmd = ArchGuardCommand(cmd_str)
        except ValueError:
            continue
        results.append(ParsedCommand(
            command=cmd,
            module_name=module,
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
