from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from archguard.dashboard.app import app
from tests.db_fixtures import requires_postgres


async def _no_op_enqueue(job_id: str) -> str:
    """Accept the job without running it.

    Submission hands the job to the queue now, so that is the seam. Patching
    the analysis itself would leave the enqueue live and start a real clone.
    """
    return "queued"


@pytest.fixture
def client():
    return TestClient(app)

@requires_postgres
def test_submit_job_invalid_url(auth_client):
    """Invalid GitHub URL → 422."""
    resp = auth_client.post("/api/jobs", json={"github_url": "not-a-url"})
    assert resp.status_code == 422

@requires_postgres
def test_submit_job_returns_202(auth_client):
    """Valid URL → 202 with job_id."""
    # Patch run_job to be a no-op (don't actually clone anything) and patch
    # fetch_repo_metadata_public so this test never makes a real network call.
    with patch("archguard.worker.queue.enqueue_analysis", new=_no_op_enqueue), \
         patch(
             "archguard.dashboard.routes.jobs.fetch_repo_metadata_public",
             return_value={"name": "flask", "full_name": "pallets/flask"},
         ):
        resp = auth_client.post(
            "/api/jobs",
            json={"github_url": "https://github.com/pallets/flask"},
        )

    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    assert body["status"] == "queued"
    assert "/api/v1/jobs/" in body["poll_url"]
    assert "/stream" in body["stream_url"]

@requires_postgres
def test_get_job_not_found(auth_client):
    """Unknown job_id -> 404.

    Also the answer for a job that exists but belongs to someone else: telling
    the two apart is what makes an id worth guessing.
    """
    resp = auth_client.get("/api/jobs/does-not-exist-12345")
    assert resp.status_code == 404

@requires_postgres
def test_get_job_status_queued(auth_client, seed_run):
    """A job this user owns shows its live status."""
    from archguard.db import store
    from archguard.db.session import session_scope
    from tests.db_fixtures import _run

    job_id = seed_run()

    async def _requeue():
        async with session_scope() as session:
            await store.set_job_status(session, job_id, "queued")

    _run(_requeue())
    resp = auth_client.get(f"/api/jobs/{job_id}")

    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"


@requires_postgres
def test_get_job_status_survives_a_restart(auth_client, seed_run):
    """Known to the database, unknown to this process -> the stored record.

    The in-memory-only version 404d here, which made every deploy look to the
    user like their analysis had been lost.
    """
    job_id = seed_run()
    resp = auth_client.get(f"/api/jobs/{job_id}")

    assert resp.status_code == 200
    assert resp.json()["job_id"] == job_id
    assert resp.json()["status"] == "complete"

@requires_postgres
def test_list_jobs_empty(auth_client):
    """A user with no jobs sees no jobs -- not everyone else's."""
    resp = auth_client.get("/api/jobs")
    assert resp.status_code == 200
    assert resp.json()["jobs"] == []


@requires_postgres
def test_submit_job_nonexistent_repo_returns_404(auth_client):
    """
    Regression test for MED-006.
    Verifies: a syntactically valid GitHub URL pointing at a repository
    that does not exist is rejected with 404 BEFORE a job is created —
    confirming the semaphore slot is never consumed for an invalid repo.
    """
    # Arrange
    jobs_before = len(auth_client.get("/api/jobs").json()["jobs"])

    # Act
    with patch(
        "archguard.dashboard.routes.jobs.fetch_repo_metadata_public",
        side_effect=ValueError("Repository owner/nonexistent-repo not found"),
    ):
        resp = auth_client.post(
            "/api/jobs",
            json={"github_url": "https://github.com/owner/nonexistent-repo"},
        )

    # Assert
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()
    # No job was created for the invalid repo — state was not corrupted
    assert len(auth_client.get("/api/jobs").json()["jobs"]) == jobs_before


@requires_postgres
def test_submit_job_github_rate_limited_still_queues(auth_client):
    """
    Verifies MED-006 fix degrades gracefully when GitHub's API rate limit
    is hit during pre-validation: per the documented design decision, the
    job is still queued (rather than blocking the user) and the actual
    clone attempt will reveal the real repository state.
    """
    from archguard.dashboard.routes.jobs import GitHubRateLimitError

    with patch("archguard.worker.queue.enqueue_analysis", new=_no_op_enqueue), \
         patch(
             "archguard.dashboard.routes.jobs.fetch_repo_metadata_public",
             side_effect=GitHubRateLimitError("GitHub API rate limit exceeded"),
         ):
        resp = auth_client.post(
            "/api/jobs",
            json={"github_url": "https://github.com/pallets/flask"},
        )

    assert resp.status_code == 202
    assert "job_id" in resp.json()


@requires_postgres
def test_validate_repo_url_rate_limited_returns_retry_after(auth_client):
    """POST /api/v1/jobs/validate surfaces GitHub's rate-limit reset as a
    Retry-After header and body field, so the UI can show a real wait time
    instead of a hardcoded guess. Pins the P0 429-handling fix.
    """
    import time

    from archguard.dashboard.routes.jobs import GitHubRateLimitError

    reset_epoch = time.time() + 90
    with patch(
        "archguard.dashboard.routes.jobs.fetch_repo_metadata_public",
        side_effect=GitHubRateLimitError("rate limited", reset_epoch=reset_epoch),
    ):
        resp = auth_client.post(
            "/api/v1/jobs/validate",
            json={"github_url": "https://github.com/pallets/flask"},
        )

    assert resp.status_code == 429
    body = resp.json()
    assert "retry_after" in body
    assert int(resp.headers["Retry-After"]) == body["retry_after"]
    # ~90s, allow a little wall-clock slack.
    assert 60 <= body["retry_after"] <= 90


@requires_postgres
def test_validate_repo_url_rate_limited_no_reset_falls_back(auth_client):
    """Without a reset epoch, a sane default Retry-After is still returned."""
    from archguard.dashboard.routes.jobs import GitHubRateLimitError

    with patch(
        "archguard.dashboard.routes.jobs.fetch_repo_metadata_public",
        side_effect=GitHubRateLimitError("rate limited"),
    ):
        resp = auth_client.post(
            "/api/v1/jobs/validate",
            json={"github_url": "https://github.com/pallets/flask"},
        )

    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) == 60
