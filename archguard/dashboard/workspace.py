"""Async workspace management for cloned repositories.

Provides an async context manager that:
1. Creates a temp directory prefixed with 'archguard-'
2. Shallow-clones the target repository into it
3. Yields the Path to the cloned repo root
4. Guarantees cleanup (shutil.rmtree) in try/finally regardless of exceptions
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

logger = logging.getLogger(__name__)

CLONE_TIMEOUT_SECONDS: int = int(os.environ.get("ARCHGUARD_CLONE_TIMEOUT", "120"))


@asynccontextmanager
async def temp_workspace(
    clone_url: str, branch: str = "HEAD", job_id: str | None = None, keep_alive: bool = False
) -> AsyncIterator[Path]:
    """Clone a repo shallowly; yield the repo root Path; clean up on exit.

    Args:
        clone_url:  HTTPS clone URL, e.g.
                    'https://github.com/pallets/flask.git'
        branch:     branch/tag to clone (default: 'HEAD' = default branch)
        job_id:     optional job identifier for predictable dir name
        keep_alive: if True, do not delete the workspace in finally block

    Raises:
        TimeoutError:   clone exceeded ARCHGUARD_CLONE_TIMEOUT seconds
        RuntimeError:   git returned non-zero exit code

    Usage::

        async with temp_workspace(clone_url) as repo_path:
            # repo_path is a Path pointing to the cloned repository root
    """
    if job_id:
        workspace_dir = Path(tempfile.gettempdir()) / f"archguard-{job_id}"
        workspace_dir.mkdir(parents=True, exist_ok=True)
    else:
        workspace_dir = Path(tempfile.mkdtemp(prefix="archguard-"))
    
    repo_path = workspace_dir / "repo"
    try:
        logger.info("Cloning %s (branch=%s) into %s", clone_url, branch, repo_path)
        await _clone_repo(clone_url, repo_path, branch)
        yield repo_path
    finally:
        if not keep_alive:
            shutil.rmtree(workspace_dir, ignore_errors=True)
            logger.info("Cleaned up workspace %s", workspace_dir)
        else:
            logger.info("Workspace %s kept alive for analysis context", workspace_dir)


async def _clone_repo(clone_url: str, dest: Path, branch: str) -> None:
    """Perform a shallow git clone.

    Uses ``--depth=1 --single-branch --no-tags`` for speed.
    When *branch* is ``'HEAD'``, omits ``--branch`` so git uses the remote
    default branch.

    Raises:
        TimeoutError: if clone exceeds CLONE_TIMEOUT_SECONDS
        RuntimeError: if git exits with non-zero status
    """
    cmd: list[str] = [
        "git", "clone",
        "--depth", "1",
        "--single-branch",
        "--no-tags",
        "--quiet",
    ]

    # Only pass --branch if a specific branch was requested
    if branch and branch != "HEAD":
        cmd.extend(["--branch", branch])

    cmd.extend([clone_url, str(dest)])

    import subprocess
    loop = asyncio.get_running_loop()

    def _do_clone() -> None:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=float(CLONE_TIMEOUT_SECONDS),
                check=False
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError(
                f"Repository clone timed out after {CLONE_TIMEOUT_SECONDS}s. "
                "The repository may be too large. "
                "Increase ARCHGUARD_CLONE_TIMEOUT to allow more time."
            )
        if proc.returncode != 0:
            error_msg = proc.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"git clone failed (exit {proc.returncode}): {error_msg[:500]}"
            )

    await loop.run_in_executor(None, _do_clone)
    logger.info("Clone completed: %s → %s", clone_url, dest)


async def cleanup_stale_workspaces(max_age_seconds: int = 3600) -> int:
    """Remove archguard-* temp directories older than *max_age_seconds*.

    Called on application startup to clean up after crashes.
    Returns the number of directories removed.
    """
    import time

    tmp = Path(tempfile.gettempdir())
    removed = 0
    now = time.time()

    for candidate in tmp.glob("archguard-*"):
        if candidate.is_dir():
            age = now - candidate.stat().st_mtime
            if age > max_age_seconds:
                shutil.rmtree(candidate, ignore_errors=True)
                logger.info(
                    "Removed stale workspace %s (age: %ds)", candidate, int(age)
                )
                removed += 1

    return removed
