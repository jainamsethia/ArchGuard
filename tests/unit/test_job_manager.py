import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archguard.dashboard.job_manager import JobManager, JobStatus

@pytest.fixture
def manager():
    return JobManager()

@pytest.mark.asyncio
async def test_concurrent_semaphore_creation_yields_single_instance() -> None:
    """
    Regression test for LOW-007.
    Verifies: calling _ensure_semaphore() concurrently from multiple
    coroutines before any semaphore exists creates exactly one semaphore
    instance with the correct concurrency limit — not multiple competing
    instances.
    """
    from archguard.dashboard.job_manager import JobManager, MAX_CONCURRENT_ANALYSES

    manager = JobManager()

    # Act: fire off many concurrent calls to _ensure_semaphore before any
    # of them has had a chance to create the semaphore yet.
    results = await asyncio.gather(
        *[manager._ensure_semaphore() for _ in range(20)]
    )

    # Assert: every call returned the exact same object (identity, not
    # just equal value) — proving only one semaphore was ever created.
    first = results[0]
    assert all(r is first for r in results)
    assert isinstance(first, asyncio.Semaphore)
    # Confirm the limit matches the configured constant by checking the
    # semaphore can be acquired exactly MAX_CONCURRENT_ANALYSES times
    # before blocking.
    acquired = []
    for _ in range(MAX_CONCURRENT_ANALYSES):
        assert first.locked() is False or len(acquired) < MAX_CONCURRENT_ANALYSES
        await first.acquire()
        acquired.append(True)
    assert first.locked() is True  # the (MAX_CONCURRENT_ANALYSES + 1)-th acquire would block
    for _ in acquired:
        first.release()


def test_create_job(manager):
    """create_job returns a job with QUEUED status and a UUID id."""
    job = manager.create_job("https://github.com/owner/repo")
    assert job.status == JobStatus.QUEUED
    assert len(job.id) == 36  # UUID4 format
    assert manager.get_job(job.id) is job

def test_list_jobs_newest_first(manager):
    """list_jobs returns jobs sorted newest-first."""
    import time
    j1 = manager.create_job("https://github.com/a/a")
    time.sleep(0.01)
    j2 = manager.create_job("https://github.com/b/b")
    jobs = manager.list_jobs()
    assert jobs[0].id == j2.id
    assert jobs[1].id == j1.id

def test_evicts_oldest_beyond_max(manager):
    """Jobs beyond MAX_STORED_JOBS are evicted (oldest first)."""
    from archguard.dashboard.job_manager import MAX_STORED_JOBS
    for i in range(MAX_STORED_JOBS + 5):
        manager.create_job(f"https://github.com/x/repo{i}")
    assert len(manager._jobs) == MAX_STORED_JOBS

@pytest.mark.asyncio
async def test_run_job_success_lifecycle(manager):
    """run_job transitions: QUEUED → CLONING → ANALYSING → COMPLETE."""
    from archguard.dashboard.pipeline_adapter import AnalysisJobResult
    job = manager.create_job("https://github.com/owner/repo")
    assert job.status == JobStatus.QUEUED
    
    mock_result = AnalysisJobResult(
        job_id="dummy",
        repo_url="dummy",
        health_score=85.0,
        health_grade="B",
        composite_score=0.15,
        total_violations=2,
        duration_seconds=10.0,
        skipped=False,
        error=None,
        layer_results=[]
    )
    
    from contextlib import asynccontextmanager
    
    @asynccontextmanager
    async def mock_ctx(url, branch="HEAD"):
        yield Path("/tmp/fake-repo")

    with patch("archguard.dashboard.workspace.temp_workspace", side_effect=lambda url, **kw: mock_ctx(url)) as mock_ws, \
         patch("archguard.dashboard.pipeline_adapter.run_analysis_on_repo", return_value=mock_result) as mock_analysis:
        
        await manager.run_job(job)
        
        assert job.status == JobStatus.COMPLETE
        assert job.result is mock_result
        assert job.completed_at is not None

@pytest.mark.asyncio
async def test_run_job_failed_on_timeout(manager):
    """TimeoutError → job.status = FAILED with error message."""
    job = manager.create_job("https://github.com/owner/repo")
    
    with patch("archguard.dashboard.workspace.temp_workspace", side_effect=TimeoutError("clone timed out")):
        await manager.run_job(job)
        
    assert job.status == JobStatus.FAILED
    assert "timed out" in job.error

@pytest.mark.asyncio
async def test_semaphore_limits_concurrency(manager):
    """At most MAX_CONCURRENT_ANALYSES jobs run simultaneously."""
    from archguard.dashboard.job_manager import MAX_CONCURRENT_ANALYSES
    concurrent_count = 0
    max_seen = 0
    
    from contextlib import asynccontextmanager
    
    @asynccontextmanager
    async def slow_job(*args, **kwargs):
        nonlocal concurrent_count, max_seen
        concurrent_count += 1
        max_seen = max(max_seen, concurrent_count)
        await asyncio.sleep(0.05)
        yield Path("/tmp/fake")
        concurrent_count -= 1

    jobs = [manager.create_job(f"https://github.com/x/r{i}") for i in range(5)]
    
    from archguard.dashboard.pipeline_adapter import AnalysisJobResult
    mock_res = AnalysisJobResult(job_id="x", repo_url="y", health_score=80.0, health_grade="B", composite_score=0.1, total_violations=0, duration_seconds=0.1, skipped=False, error=None, layer_results=[])
    
    with patch("archguard.dashboard.workspace.temp_workspace", side_effect=slow_job), \
         patch("archguard.dashboard.pipeline_adapter.run_analysis_on_repo", return_value=mock_res):
        
        tasks = [asyncio.create_task(manager.run_job(j)) for j in jobs]
        await asyncio.gather(*tasks)
        
    assert max_seen <= MAX_CONCURRENT_ANALYSES


@pytest.mark.asyncio
async def test_run_job_uses_safe_reconstructed_clone_url(monkeypatch) -> None:
    """
    Regression test for CRIT-001.
    Verifies: run_job never passes job.github_url directly to
    temp_workspace — it always passes the output of build_safe_clone_url,
    even when job.github_url contains a trailing slash or missing .git
    suffix that would previously have been hand-patched in run_job itself.
    """
    from archguard.dashboard.job_manager import JobManager, AnalysisJob
    from contextlib import asynccontextmanager
    from pathlib import Path

    captured_clone_url = {}

    @asynccontextmanager
    async def _fake_temp_workspace(clone_url, *, job_id, keep_alive):
        captured_clone_url["url"] = clone_url
        raise TimeoutError("stop before actual clone — test only needs the URL")
        yield Path("/tmp/fake-repo")

    monkeypatch.setattr(
        "archguard.dashboard.workspace.temp_workspace", _fake_temp_workspace
    )

    manager = JobManager()
    job = AnalysisJob(github_url="https://github.com/pallets/flask")

    # Act
    await manager.run_job(job)

    # Assert
    assert captured_clone_url["url"] == "https://github.com/pallets/flask.git"


@pytest.mark.asyncio
async def test_run_job_rejects_malformed_github_url_safely() -> None:
    """
    Verifies CRIT-001 fix degrades gracefully when a job somehow carries a
    malformed github_url (e.g. constructed directly, bypassing the API's
    own parse_github_url validation at submission time): the job is marked
    FAILED with a clear error rather than the malformed URL ever reaching
    a clone call.
    """
    from archguard.dashboard.job_manager import JobManager, AnalysisJob, JobStatus

    manager = JobManager()
    job = AnalysisJob(github_url="not-a-valid-url-at-all")

    # Act
    await manager.run_job(job)

    # Assert: job failed, no clone was attempted, state is not corrupted
    assert job.status == JobStatus.FAILED
    assert job.error is not None
    assert "Cannot parse GitHub URL" in job.error
