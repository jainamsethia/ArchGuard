"""Integration tests for workspace lifecycle management.

Covers:
- temp_workspace context manager (create + clone + cleanup)
- cleanup_stale_workspaces (startup/periodic eviction)
- get_target_path workspace resolution
"""

from __future__ import annotations

import os
import time
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from archguard.dashboard.workspace import cleanup_stale_workspaces


# ── cleanup_stale_workspaces ────────────────────────────────────────────────


import asyncio


def _cleanup(max_age_seconds=3600):
    """Sync wrapper around the async cleanup_stale_workspaces."""
    return asyncio.run(cleanup_stale_workspaces(max_age_seconds=max_age_seconds))


def test_cleanup_removes_old_workspaces():
    """cleanup_stale_workspaces removes archguard-* dirs older than max_age."""
    tmp = Path(tempfile.gettempdir())
    old_dir = tmp / "archguard-test-old-workspace"
    old_dir.mkdir(parents=True, exist_ok=True)
    # Set mtime to 2 hours ago
    ancient = time.time() - 7200
    os.utime(old_dir, (ancient, ancient))

    try:
        removed = _cleanup(max_age_seconds=3600)
        assert removed >= 1
        assert not old_dir.exists()
    finally:
        if old_dir.exists():
            import shutil
            shutil.rmtree(old_dir, ignore_errors=True)


def test_cleanup_preserves_recent_workspaces():
    """cleanup_stale_workspaces does NOT remove recent archguard-* dirs."""
    tmp = Path(tempfile.gettempdir())
    new_dir = tmp / "archguard-test-fresh-workspace"
    new_dir.mkdir(parents=True, exist_ok=True)

    try:
        removed = _cleanup(max_age_seconds=3600)
        assert new_dir.exists(), "Recent workspace should not have been removed"
    finally:
        import shutil
        shutil.rmtree(new_dir, ignore_errors=True)


def test_cleanup_handles_empty_temp_dir():
    """cleanup_stale_workspaces does not error when no archguard-* dirs exist."""
    removed = _cleanup(max_age_seconds=1)
    assert isinstance(removed, int)


# ── get_target_path ─────────────────────────────────────────────────────────


def test_get_target_path_with_valid_job_id(tmp_path):
    """get_target_path returns workspace path when directory exists."""
    from archguard.dashboard.app import get_target_path

    import uuid
    job_id = str(uuid.uuid4())
    workspace = Path(tempfile.gettempdir()) / f"archguard-{job_id}" / "repo"
    workspace.mkdir(parents=True, exist_ok=True)

    try:
        result = get_target_path(job_id)
        assert result == workspace
    finally:
        import shutil
        shutil.rmtree(workspace.parent, ignore_errors=True)


def test_get_target_path_without_job_id():
    """get_target_path returns CWD when no job_id is provided."""
    from archguard.dashboard.app import get_target_path
    result = get_target_path(None)
    assert result == Path.cwd()


def test_get_target_path_invalid_uuid_rejected():
    """get_target_path raises 400 for malformed job_id strings."""
    from archguard.dashboard.app import get_target_path
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        get_target_path("../../../etc/passwd")
    assert excinfo.value.status_code == 400
