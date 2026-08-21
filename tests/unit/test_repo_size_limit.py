"""Repository size limit at job submission (D3).

``RepoMetadata.size_kb`` was fetched from the GitHub API on every submission and
then discarded, so nothing bounded how large a repository a caller could
enqueue. Workspaces are cloned with ``keep_alive=True`` and the age-based sweep
exempts every job that has not finished, so a handful of large repositories
filled the analysis host's disk and took the service down.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from archguard.dashboard.app import app

# GitHub reports repository size in kilobytes.
_TWO_GB_IN_KB = 2 * 1024 * 1024


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def never_runs_a_job() -> Iterator[Any]:
    """Submission must never reach the clone/analyse path in these tests."""
    with (
        patch("archguard.worker.queue.enqueue_analysis") as enqueue,
    ):
        yield enqueue


def _metadata(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "flask",
        "full_name": "pallets/flask",
        "size": 5_000,
    }
    data.update(overrides)
    return data


def test_oversized_repository_is_rejected_with_413(
    client: TestClient, never_runs_a_job: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("archguard.dashboard.routes.jobs.MAX_REPO_SIZE_KB", 512_000)
    with patch(
        "archguard.dashboard.routes.jobs.fetch_repo_metadata_public",
        return_value=_metadata(size=_TWO_GB_IN_KB),
    ):
        resp = client.post(
            "/api/v1/jobs", json={"github_url": "https://github.com/torvalds/linux"}
        )

    assert resp.status_code == 413
    detail = resp.json()["detail"]
    # The message has to be actionable: it must say how big the repo is and
    # what the ceiling is, or the user cannot tell why they were refused.
    assert "2048" in detail or "2.0" in detail
    assert "500" in detail
    never_runs_a_job.assert_not_called()


def test_repository_within_the_limit_is_accepted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("archguard.dashboard.routes.jobs.MAX_REPO_SIZE_KB", 512_000)
    with (
        patch(
            "archguard.dashboard.routes.jobs.fetch_repo_metadata_public",
            return_value=_metadata(size=5_000),
        ),
    ):
        resp = client.post(
            "/api/v1/jobs", json={"github_url": "https://github.com/pallets/flask"}
        )

    assert resp.status_code == 202


def test_limit_of_zero_disables_the_check(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Self-hosted operators with their own disk budget need an escape hatch."""
    monkeypatch.setattr("archguard.dashboard.routes.jobs.MAX_REPO_SIZE_KB", 0)
    with (
        patch(
            "archguard.dashboard.routes.jobs.fetch_repo_metadata_public",
            return_value=_metadata(size=_TWO_GB_IN_KB),
        ),
    ):
        resp = client.post(
            "/api/v1/jobs", json={"github_url": "https://github.com/torvalds/linux"}
        )

    assert resp.status_code == 202


def test_metadata_without_a_size_field_is_not_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size gate must not turn an unexpected API payload into a refusal.

    tests/unit/test_jobs_endpoints.py patches this call with a payload carrying
    no "size" key at all, which is exactly the shape a future GitHub API change
    could produce.
    """
    monkeypatch.setattr("archguard.dashboard.routes.jobs.MAX_REPO_SIZE_KB", 512_000)
    metadata = _metadata()
    del metadata["size"]
    with (
        patch(
            "archguard.dashboard.routes.jobs.fetch_repo_metadata_public",
            return_value=metadata,
        ),
    ):
        resp = client.post(
            "/api/v1/jobs", json={"github_url": "https://github.com/pallets/flask"}
        )

    assert resp.status_code == 202


def test_a_non_numeric_size_is_not_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("archguard.dashboard.routes.jobs.MAX_REPO_SIZE_KB", 512_000)
    with (
        patch(
            "archguard.dashboard.routes.jobs.fetch_repo_metadata_public",
            return_value=_metadata(size="unknown"),
        ),
    ):
        resp = client.post(
            "/api/v1/jobs", json={"github_url": "https://github.com/pallets/flask"}
        )

    assert resp.status_code == 202


def test_rate_limited_metadata_lookup_still_enqueues(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When GitHub rate-limits us we have no size to check.

    The pre-existing behaviour is to let the job through rather than block the
    user; the size gate must not silently turn that into a refusal. The clone
    timeout remains the backstop.
    """
    from archguard.dashboard.routes.jobs import GitHubRateLimitError

    monkeypatch.setattr("archguard.dashboard.routes.jobs.MAX_REPO_SIZE_KB", 512_000)
    with (
        patch(
            "archguard.dashboard.routes.jobs.fetch_repo_metadata_public",
            side_effect=GitHubRateLimitError("rate limited"),
        ),
    ):
        resp = client.post(
            "/api/v1/jobs", json={"github_url": "https://github.com/pallets/flask"}
        )

    assert resp.status_code == 202
    assert resp.json()["validation_skipped_rate_limit"] is True
