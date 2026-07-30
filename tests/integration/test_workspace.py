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


def test_cleanup_exempts_active_jobs_on_noatime_filesystem():
    """A workspace whose job is still in memory survives the periodic sweep.

    Both timestamps are frozen in the past to emulate a ``noatime``/``relatime``
    mount, where no amount of reading advances the times. Liveness must come
    from the job manager, not the filesystem, or a user reading results gets
    their workspace deleted mid-session.
    """
    import shutil

    tmp = Path(tempfile.gettempdir())
    live_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    orphan_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    live = tmp / f"archguard-{live_id}"
    orphan = tmp / f"archguard-{orphan_id}"

    try:
        ancient = time.time() - 1200  # 20 min, older than the 15 min sweep
        for d in (live, orphan):
            (d / "repo").mkdir(parents=True, exist_ok=True)
            os.utime(d, (ancient, ancient))

        removed = asyncio.run(
            cleanup_stale_workspaces(
                max_age_seconds=900, active_job_ids={live_id}
            )
        )

        assert live.exists(), "workspace of a live job must not be reaped"
        assert not orphan.exists(), "genuinely orphaned workspace must be reclaimed"
        assert removed >= 1
    finally:
        for d in (live, orphan):
            shutil.rmtree(d, ignore_errors=True)


def test_cleanup_without_active_ids_still_reaps_everything_stale():
    """The startup sweep passes no active IDs: after a crash nothing is live."""
    import shutil

    tmp = Path(tempfile.gettempdir())
    d = tmp / "archguard-eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    try:
        (d / "repo").mkdir(parents=True, exist_ok=True)
        ancient = time.time() - 1200
        os.utime(d, (ancient, ancient))

        asyncio.run(cleanup_stale_workspaces(max_age_seconds=900))

        assert not d.exists()
    finally:
        shutil.rmtree(d, ignore_errors=True)


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
