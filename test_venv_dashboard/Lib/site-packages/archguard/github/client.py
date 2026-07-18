"""GitHub client wrapper for ArchGuard using requests."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, cast

import httpx

from archguard.utils.errors import ConfigError
from archguard.utils.retry import with_retry

logger = logging.getLogger(__name__)


class RateLimitExceededException(Exception):
    pass


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
    """GitHub client for PR interactions using standard requests."""

    def __init__(self, token: str | None = None) -> None:
        token = token or os.environ.get("GITHUB_TOKEN")
        if not token:
            raise ConfigError("GITHUB_TOKEN environment variable not set.")
        self._token = token
        self._headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._http_client = httpx.Client(
            headers=self._headers,
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=None),
        )
        self._validate_token_scopes(token)

    def _validate_token_scopes(self, token: str) -> None:
        try:
            resp = self._http_client.get("https://api.github.com/user", timeout=5.0)
            if resp.status_code == 200:
                scopes = resp.headers.get("X-OAuth-Scopes", "")
                if "repo" not in scopes and "public_repo" not in scopes:
                    raise ConfigError(
                        f"GITHUB_TOKEN has insufficient scopes: {scopes}. Needs 'repo' or 'public_repo'."
                    )
        except httpx.RequestError as e:
            logger.warning(f"Could not validate GitHub token scopes: {e}")

    def _check_rate_limit(self) -> None:
        """Pre-flight rate limit check. Raises RateLimitExceededException if critical."""
        try:
            resp = self._http_client.get(
                "https://api.github.com/rate_limit", timeout=5.0
            )
            if resp.status_code == 200:
                data = resp.json()
                remaining = (
                    data.get("resources", {}).get("core", {}).get("remaining", 5000)
                )
                reset_time = int(
                    data.get("resources", {}).get("core", {}).get("reset", 0)
                )
                if remaining < 50:
                    raise RateLimitExceededException(
                        f"GitHub API rate limit critical: {remaining} remaining. "
                        f"Resets at {reset_time}. "
                        f"Set ARCHGUARD_SKIP_COMMENT=1 to bypass PR posting."
                    )
        except RateLimitExceededException:
            raise
        except Exception as e:
            logger.warning("Failed to check rate limit: %s", e)

    @with_retry(max_attempts=3)
    def _get_api(self, url: str, check_rate: bool = True) -> Any:
        if check_rate:
            self._check_rate_limit()
        resp = self._http_client.get(url, timeout=10.0)
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            raise RateLimitExceededException("Rate limit exceeded")
        resp.raise_for_status()
        return resp.json()

    def get_pr(self, repo_slug: str, pr_number: int) -> Any:
        """Return PR info dict."""
        url = f"https://api.github.com/repos/{repo_slug}/pulls/{pr_number}"
        try:
            return self._get_api(url)
        except RateLimitExceededException as e:
            logger.warning(f"Rate limit exceeded during get_pr: {e}")
            return {}

    def get_pr_changed_files(
        self,
        repo_slug: str,
        pr_number: int,
    ) -> list[str]:
        """Return list of changed file paths in the PR."""
        try:
            self._check_rate_limit()
        except RateLimitExceededException as e:
            logger.warning(f"Rate limit exceeded during get_pr_changed_files: {e}")
            return []

        all_filenames: list[str] = []
        page = 1
        max_files = 3000

        while len(all_filenames) < max_files:
            url = f"https://api.github.com/repos/{repo_slug}/pulls/{pr_number}/files?page={page}&per_page=100"
            files = self._get_api(url, check_rate=False)

            if not files:
                break

            for f in files:
                if "filename" in f:
                    all_filenames.append(f["filename"])

            page += 1

        return all_filenames

    def is_collaborator(self, repo_slug: str, username: str) -> bool:
        """Return ``True`` if *username* has write access to *repo_slug*."""
        try:
            self._check_rate_limit()
            url = f"https://api.github.com/repos/{repo_slug}/collaborators/{username}"
            resp = self._http_client.get(url, timeout=5.0)
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                raise RateLimitExceededException("Rate limit exceeded")
            return bool(resp.status_code == 204)
        except Exception as e:
            logger.warning(f"Non-critical failure in is_collaborator: {e}")
            return False

    @with_retry(max_attempts=3)
    def post_comment(
        self,
        repo_slug: str,
        body: str,
        pr_number: int | None = None,
    ) -> bool:
        """Create a new comment on the PR."""
        if pr_number is None:
            pr_number = _get_pr_number()
        if pr_number is None:
            return False

        try:
            self._check_rate_limit()
            url = (
                f"https://api.github.com/repos/{repo_slug}/issues/{pr_number}/comments"
            )
            resp = self._http_client.post(
                url,
                json={"body": body},
                timeout=10.0,
            )
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                raise RateLimitExceededException("Rate limit exceeded")
            resp.raise_for_status()
            return True
        except RateLimitExceededException as e:
            logger.warning(f"Skipping post_comment due to rate limit: {e}")
            return False

    def get_issue_comments(
        self, repo_slug: str, pr_number: int
    ) -> list[dict[str, Any]]:
        url = f"https://api.github.com/repos/{repo_slug}/issues/{pr_number}/comments"
        try:
            return cast(list[dict[str, Any]], self._get_api(url))
        except RateLimitExceededException as e:
            logger.warning(f"Rate limit exceeded during get_issue_comments: {e}")
            return []

    def update_comment(self, repo_slug: str, comment_id: int, body: str) -> bool:
        try:
            self._check_rate_limit()
            url = (
                f"https://api.github.com/repos/{repo_slug}/issues/comments/{comment_id}"
            )
            resp = self._http_client.patch(
                url,
                json={"body": body},
                timeout=10.0,
            )
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                raise RateLimitExceededException("Rate limit exceeded")
            resp.raise_for_status()
            return True
        except RateLimitExceededException as e:
            logger.warning(f"Skipping update_comment due to rate limit: {e}")
            return False

    def delete_comment(self, repo_slug: str, comment_id: int) -> bool:
        try:
            self._check_rate_limit()
            url = (
                f"https://api.github.com/repos/{repo_slug}/issues/comments/{comment_id}"
            )
            resp = self._http_client.delete(
                url,
                timeout=10.0,
            )
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                raise RateLimitExceededException("Rate limit exceeded")
            resp.raise_for_status()
            return True
        except RateLimitExceededException as e:
            logger.warning(f"Skipping delete_comment due to rate limit: {e}")
            return False


def post_comment(
    repo_slug: str,
    body: str,
    pr_number: int | None = None,
    token: str | None = None,
) -> bool:
    try:
        client = GitHubClient(token=token)
        return client.post_comment(repo_slug, body, pr_number=pr_number)
    except Exception as e:
        logger.error(f"Failed to post comment: {e}")
        return False
