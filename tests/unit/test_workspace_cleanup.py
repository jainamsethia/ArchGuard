import pytest
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock


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
    import time, os
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
