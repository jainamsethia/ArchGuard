"""PyGitHub wrapper for ArchGuard."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from archguard.utils.errors import ConfigError

logger = logging.getLogger(__name__)


def _get_pr_number() -> int | None:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    try:
        with open(event_path) as f:
            event = json.load(f)
        return event.get("pull_request", {}).get("number") or event.get("number")
    except (OSError, json.JSONDecodeError, KeyError):
        return None



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
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(f"Non-critical failure in is_collaborator: {e}")
            return False

    def get_repo(self, repo_slug: str) -> Any:
        """Return a PyGitHub Repository object."""
        return self._gh.get_repo(repo_slug)

    from archguard.utils.retry import with_retry
    import requests

    @with_retry(max_attempts=3, retryable_exceptions=(requests.exceptions.RequestException,))
    def _do_post(self, repo_slug: str, body: str, pr_number: int) -> None:
        from archguard.github.comments import PRCommentManager
        manager = PRCommentManager(self)
        manager.post_or_update(repo_slug, pr_number, body)

    def post_comment(
        self,
        repo_slug: str,
        body: str,
        pr_number: int | None = None,
    ) -> bool:
        """Post or update a comment on a GitHub PR."""
        pr_number = pr_number or _get_pr_number()
        if pr_number is None:
            logger.warning("Could not determine PR number. Skipping comment posting.")
            return False

        try:
            self._do_post(repo_slug, body, pr_number)
            return True
        except Exception as e:
            logger.warning("Failed to post PR comment: %s", e)
            return False


def post_comment(
    repo_slug: str,
    body: str,
    pr_number: int | None = None,
    token: str | None = None,
) -> bool:
    """Post or update a comment on a GitHub PR."""
    pr_number = pr_number or _get_pr_number()
    if pr_number is None:
        logger.warning("Could not determine PR number. Skipping comment posting.")
        return False

    try:
        client = GitHubClient(token=token)
        return client.post_comment(repo_slug, body, pr_number=pr_number)
    except Exception as e:
        logger.warning("Failed to post PR comment: %s", e)
        return False
