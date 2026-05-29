"""PyGitHub wrapper for ArchGuard."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

from archguard.utils.errors import ConfigError
from archguard.utils.retry import with_retry, exponential_backoff
from github import GithubException, RateLimitExceededException
import time

logger = logging.getLogger(__name__)


def _get_pr_number() -> int | None:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    try:
        with open(event_path) as f:
            event = json.load(f)
        val = event.get("pull_request", {}).get("number") or event.get("number")
        return int(val) if val is not None else None
    except (OSError, json.JSONDecodeError, KeyError):
        return None



class GitHubClient:
    """Thin wrapper around PyGitHub for PR interactions."""

    def __init__(self, token: str | None = None) -> None:
        token = token or os.environ.get("GITHUB_TOKEN")
        if not token:
            raise ConfigError("GITHUB_TOKEN environment variable not set.")
        self._validate_token_scopes(token)
        from github import Github  # lazy import

        self._gh: Any = Github(token)

    def _validate_token_scopes(self, token: str) -> None:
        import requests
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}
        try:
            resp = requests.get("https://api.github.com/user", headers=headers, timeout=5)
            if resp.status_code == 200:
                scopes = resp.headers.get("X-OAuth-Scopes", "")
                if "repo" not in scopes and "public_repo" not in scopes:
                    raise ConfigError(f"GITHUB_TOKEN has insufficient scopes: {scopes}. Needs 'repo' or 'public_repo'.")
        except requests.exceptions.RequestException as e:
            logger.warning(f"Could not validate GitHub token scopes: {e}")


    def _check_rate_limit(self) -> None:
        """Pre-flight rate limit check. Wait if below threshold."""
        try:
            rate_limit = self._gh.get_rate_limit()
            if rate_limit.core.remaining < 50:
                reset_time = rate_limit.core.reset.timestamp()
                wait_seconds = max(0, reset_time - time.time() + 5)
                if wait_seconds > 0:
                    logger.warning(
                        "GitHub API rate limit low (%d remaining). Waiting %ds for reset.",
                        rate_limit.core.remaining, wait_seconds
                    )
                    time.sleep(min(wait_seconds, 300))  # cap at 5 min wait
        except Exception as e:
            logger.warning(f"Failed to check rate limit: {e}")

    @exponential_backoff(max_retries=3)
    def get_pr(self, repo_slug: str, pr_number: int) -> Any:
        """Return a PyGitHub PullRequest object."""
        self._check_rate_limit()
        repo = self._gh.get_repo(repo_slug)
        return repo.get_pull(pr_number)

    @exponential_backoff(max_retries=3)
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
            self._check_rate_limit()
            repo = self._gh.get_repo(repo_slug)
            return bool(repo.has_in_collaborators(username))
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(f"Non-critical failure in is_collaborator: {e}")
            return False

    @exponential_backoff(max_retries=3)
    def get_repo(self, repo_slug: str) -> Any:
        """Return a PyGitHub Repository object."""
        self._check_rate_limit()
        return self._gh.get_repo(repo_slug)

    @exponential_backoff(
        max_retries=5,
        retryable_exceptions=(GithubException, ConnectionError, requests.exceptions.RequestException),
        retryable_status_codes=(429, 500, 502, 503, 504)
    )
    def _do_post(self, repo_slug: str, body: str, pr_number: int) -> None:
        self._check_rate_limit()
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
