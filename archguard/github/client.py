"""PyGitHub wrapper for ArchGuard."""

from __future__ import annotations

import os
from typing import Any

from archguard.utils.errors import ConfigError


class GitHubClient:
    """Thin wrapper around PyGitHub for PR interactions."""

    def __init__(self, token: str | None = None) -> None:
        token = token or os.environ.get("GITHUB_TOKEN")
        if not token:
            raise ConfigError("GITHUB_TOKEN environment variable not set.")
        from github import Github  # lazy import

        self._gh: Any = Github(token)

    def get_pr(self, repo_slug: str, pr_number: int) -> Any:
        """Return a PyGitHub PullRequest object."""
        repo = self._gh.get_repo(repo_slug)
        return repo.get_pull(pr_number)

    def get_pr_changed_files(
        self,
        repo_slug: str,
        pr_number: int,
    ) -> list[str]:
        """Return list of changed file paths in the PR."""
        pr = self.get_pr(repo_slug, pr_number)
        return [f.filename for f in pr.get_files()]

    def is_collaborator(self, repo_slug: str, username: str) -> bool:
        """Return ``True`` if *username* has write access to *repo_slug*."""
        try:
            repo = self._gh.get_repo(repo_slug)
            return bool(repo.has_in_collaborators(username))
        except Exception:  # noqa: BLE001
            return False

    def get_repo(self, repo_slug: str) -> Any:
        """Return a PyGitHub Repository object."""
        return self._gh.get_repo(repo_slug)
