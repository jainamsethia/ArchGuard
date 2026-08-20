import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_workspace_keep_alive_false_does_not_raise():
    """Regression for MED-01: keep_alive=False must not raise; workspace context exits cleanly."""
    # Arrange — patch clone so we don't actually git clone
    from archguard.dashboard.workspace import temp_workspace
    with patch("archguard.dashboard.workspace._clone_repo",
               new_callable=AsyncMock) as mock_clone:
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_ws = Path(tmpdir) / "archguard-test-job"
            fake_ws.mkdir()
            (fake_ws / "repo").mkdir()
            mock_clone.return_value = None
            # Act
            async with temp_workspace(
                "https://github.com/octocat/Hello-World",
                job_id="test-job-keep-alive-false",
                keep_alive=False
            ) as repo_path:
                assert repo_path is not None
            # Assert: context exited without exception (keep_alive=False is safe)


@pytest.mark.asyncio
async def test_cleanup_stale_workspaces_removes_old_dirs():
    """Regression for MED-01/IMPROVEMENT-02: stale workspaces older than max_age are removed."""
    import os
    import time

    from archguard.dashboard.workspace import cleanup_stale_workspaces
    with tempfile.TemporaryDirectory() as tmpdir:
        stale_ws = Path(tmpdir) / "archguard-stale-job"
        stale_ws.mkdir()
        # Set mtime to 2 hours ago
        old_time = time.time() - 7200
        os.utime(stale_ws, (old_time, old_time))
        with patch("archguard.dashboard.workspace.tempfile.gettempdir", return_value=tmpdir):
            # Act
            await cleanup_stale_workspaces(max_age_seconds=3600)
        # Assert
        assert not stale_ws.exists(), "Stale workspace should have been removed"


# ---------------------------------------------------------------------------
# Disk budget (D3)
#
# The age sweep alone does not bound disk: it exempts every job the manager
# still knows about, which is up to MAX_STORED_JOBS (50). Fifty completed jobs
# holding large clones therefore sit on disk indefinitely. The budget below is
# the ceiling that actually binds, and it protects only *running* jobs -- a
# finished job's clone is a cache for browsing results, and the endpoints
# already fall back to the persisted run when it is gone.
# ---------------------------------------------------------------------------


def _make_workspace(root: Path, job_id: str, size_bytes: int, age_seconds: float) -> Path:
    import os
    import time

    ws = root / f"archguard-{job_id}"
    (ws / "repo").mkdir(parents=True)
    (ws / "repo" / "blob.bin").write_bytes(b"\0" * size_bytes)
    stamp = time.time() - age_seconds
    os.utime(ws, (stamp, stamp))
    return ws


@pytest.mark.asyncio
async def test_budget_evicts_oldest_workspaces_until_under_the_ceiling():
    from archguard.dashboard.workspace import enforce_workspace_budget

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        oldest = _make_workspace(root, "aaa", 4096, age_seconds=900)
        middle = _make_workspace(root, "bbb", 4096, age_seconds=600)
        newest = _make_workspace(root, "ccc", 4096, age_seconds=60)

        with patch("archguard.dashboard.workspace.tempfile.gettempdir", return_value=tmpdir):
            evicted, reclaimed = await enforce_workspace_budget(
                max_bytes=9000, active_job_ids=set()
            )

        assert evicted == 1, "one eviction should bring 3x4096 under a 9000-byte ceiling"
        assert reclaimed >= 4096
        assert not oldest.exists(), "the oldest workspace should go first"
        assert middle.exists()
        assert newest.exists()


@pytest.mark.asyncio
async def test_budget_never_evicts_a_running_job():
    from archguard.dashboard.workspace import enforce_workspace_budget

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        running = _make_workspace(root, "aaa", 8192, age_seconds=900)
        finished = _make_workspace(root, "bbb", 8192, age_seconds=60)

        with patch("archguard.dashboard.workspace.tempfile.gettempdir", return_value=tmpdir):
            evicted, _ = await enforce_workspace_budget(
                max_bytes=1000, active_job_ids={"aaa"}
            )

        assert evicted == 1
        assert running.exists(), "a running job's clone must survive disk pressure"
        assert not finished.exists()


@pytest.mark.asyncio
async def test_budget_is_a_noop_when_under_the_ceiling():
    from archguard.dashboard.workspace import enforce_workspace_budget

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        ws = _make_workspace(root, "aaa", 1024, age_seconds=900)

        with patch("archguard.dashboard.workspace.tempfile.gettempdir", return_value=tmpdir):
            evicted, reclaimed = await enforce_workspace_budget(
                max_bytes=10_000_000, active_job_ids=set()
            )

        assert (evicted, reclaimed) == (0, 0)
        assert ws.exists()


@pytest.mark.asyncio
async def test_budget_of_zero_disables_the_ceiling():
    from archguard.dashboard.workspace import enforce_workspace_budget

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        ws = _make_workspace(root, "aaa", 8192, age_seconds=900)

        with patch("archguard.dashboard.workspace.tempfile.gettempdir", return_value=tmpdir):
            evicted, _ = await enforce_workspace_budget(max_bytes=0, active_job_ids=set())

        assert evicted == 0
        assert ws.exists()


@pytest.mark.asyncio
async def test_budget_reports_when_it_cannot_reclaim_enough(caplog):
    """Every workspace protected and still over budget is worth saying out loud:
    it is the state that precedes filling the disk."""
    import logging

    from archguard.dashboard.workspace import enforce_workspace_budget

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _make_workspace(root, "aaa", 8192, age_seconds=900)

        with (
            patch("archguard.dashboard.workspace.tempfile.gettempdir", return_value=tmpdir),
            caplog.at_level(logging.WARNING, logger="archguard.dashboard.workspace"),
        ):
            evicted, _ = await enforce_workspace_budget(
                max_bytes=1000, active_job_ids={"aaa"}
            )

        assert evicted == 0
        assert "still over" in caplog.text.lower()
