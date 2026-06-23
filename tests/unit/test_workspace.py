"""Tests for async workspace management (temp_workspace, _clone_repo, cleanup)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archguard.dashboard.workspace import (
    _clone_repo,
    cleanup_stale_workspaces,
    temp_workspace,
)


@pytest.mark.asyncio
async def test_temp_workspace_yields_path_and_cleans_up() -> None:
    """temp_workspace should yield a Path and delete it afterward."""

    async def fake_clone(url: str, dest: Path, branch: str) -> None:
        dest.mkdir(parents=True)

    with patch("archguard.dashboard.workspace._clone_repo", side_effect=fake_clone):
        captured_path: Path | None = None
        async with temp_workspace("https://github.com/example/repo.git") as repo_path:
            captured_path = repo_path
            # During context — workspace dir exists
            assert captured_path.parent.exists()

        # After context exits, the parent temp dir should be gone
        assert captured_path is not None
        assert not captured_path.parent.exists()


@pytest.mark.asyncio
async def test_temp_workspace_cleans_up_on_exception() -> None:
    """Cleanup must happen even when the body raises."""

    async def fake_clone(url: str, dest: Path, branch: str) -> None:
        dest.mkdir(parents=True)

    workspace_ref: list[Path] = []

    with patch("archguard.dashboard.workspace._clone_repo", side_effect=fake_clone):
        with pytest.raises(ValueError, match="body error"):
            async with temp_workspace("https://github.com/example/repo.git") as p:
                workspace_ref.append(p.parent)
                raise ValueError("body error")

    assert workspace_ref, "workspace dir should have been captured"
    assert not workspace_ref[0].exists(), "workspace dir should be cleaned up"


@pytest.mark.asyncio
async def test_clone_repo_timeout() -> None:
    """_clone_repo should raise TimeoutError if process hangs."""

    async def hanging_communicate() -> tuple[bytes, bytes]:
        await asyncio.sleep(9999)
        return b"", b""

    mock_proc = MagicMock()
    mock_proc.communicate = hanging_communicate
    mock_proc.kill = MagicMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with patch("archguard.dashboard.workspace.CLONE_TIMEOUT_SECONDS", 0):
            with pytest.raises(TimeoutError, match="timed out"):
                await _clone_repo(
                    "https://github.com/example/repo.git",
                    Path("/tmp/test"),
                    "HEAD",
                )


@pytest.mark.asyncio
async def test_clone_repo_failure() -> None:
    """_clone_repo should raise RuntimeError on non-zero exit code."""
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"fatal: not found"))
    mock_proc.returncode = 128
    mock_proc.kill = MagicMock()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with pytest.raises(RuntimeError, match="git clone failed"):
            await _clone_repo(
                "https://github.com/bad/repo.git",
                Path("/tmp/test"),
                "HEAD",
            )


@pytest.mark.asyncio
async def test_cleanup_stale_workspaces(tmp_path: Path) -> None:
    """cleanup_stale_workspaces removes old archguard-* dirs only."""
    import shutil
    import tempfile

    # Create a fake stale workspace in the system temp dir
    stale_dir = Path(tempfile.mkdtemp(prefix="archguard-"))
    # Backdate its mtime so it appears old
    old_time = time.time() - 7200  # 2 hours ago
    import os

    os.utime(stale_dir, (old_time, old_time))

    try:
        removed = await cleanup_stale_workspaces(max_age_seconds=3600)
        assert removed >= 1
        assert not stale_dir.exists()
    finally:
        # Safety net in case test fails
        shutil.rmtree(stale_dir, ignore_errors=True)
