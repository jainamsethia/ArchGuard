"""Async workspace management for cloned repositories.

Provides an async context manager that:
1. Creates a temp directory prefixed with 'archguard-'
2. Blobless-clones the target repository into it, retaining full commit history
3. Yields the Path to the cloned repo root
4. Guarantees cleanup (shutil.rmtree) in try/finally regardless of exceptions

The clone is deliberately *not* shallow.  Contract generation derives module
boundaries from co-change history (see ``archguard.cli._init_phases``), and a
truncated history silently degrades that to guessing modules from directory
names.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

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


def _find_git() -> str | None:
    """Find a working git executable, preferring x64 over ARM64 on Windows.

    On Windows, ``shutil.which(\"git\")`` can return the ARM64 (clangarm64) binary
    which crashes with STATUS_DLL_INIT_FAILED on x64 hardware.  This checks the
    standard x64 paths first before falling back to ``PATH`` lookup.
    """
    # Preferred x64/mingw64 paths (Windows)
    preferred = [
        r"C:\Program Files\Git\bin\git.exe",
        r"C:\Program Files\Git\mingw64\bin\git.exe",
        r"C:\Program Files\Git\cmd\git.exe",
    ]
    for p in preferred:
        if os.path.isfile(p):
            return p
    # Fall back to PATH lookup (may pick ARM64; better than nothing)
    found = shutil.which("git")
    if found and "clangarm64" not in found:
        return found
    # Last resort: try PATH even if it's ARM64
    return found


async def _clone_repo(clone_url: str, dest: Path, branch: str) -> None:
    """Perform a blobless, full-history git clone.

    Uses ``--filter=blob:none --single-branch --no-tags`` for speed.  This keeps
    every commit (history is what module detection is derived from) while
    skipping historical file *contents*, which are never read: the co-change
    graph is built with ``git log --name-only --no-renames``, which needs only
    commit and tree objects.  Blobs for the checked-out revision are fetched by
    the clone itself, so the working tree is complete.

    Do not add ``--depth`` here.  It would truncate history and silently push
    contract generation onto the directory-name heuristic.

    When *branch* is ``'HEAD'``, omits ``--branch`` so git uses the remote
    default branch.

    Raises:
        TimeoutError: if clone exceeds CLONE_TIMEOUT_SECONDS
        RuntimeError: if git exits with non-zero status
    """
    import os
    import subprocess

    # On Windows, find git -- prefer x64/mingw64 over ARM64 (clangarm64)
    git_exe = _find_git()
    if not git_exe:
        raise RuntimeError("git executable not found in PATH")
    git_exe = os.path.abspath(git_exe)

    cmd: list[str] = [
        git_exe, "clone",
        "--filter=blob:none",
        "--single-branch",
        "--no-tags",
        "--quiet",
    ]

    # Only pass --branch if a specific branch was requested
    if branch and branch != "HEAD":
        cmd.extend(["--branch", branch])

    cmd.extend([clone_url, str(dest)])

    # We do NOT manipulate PATH to guess DLL locations here.
    # Passing env=None relies on the OS and the wrapper's own internal
    # resolution (which correctly locates msys-2.0.dll etc.).
    # Modifying PATH manually causes git clone failures (exit 3221225794)
    # on non-standard installations (like MSYS2 or ARM64 Git) by breaking
    # DLL search order.

    logger.info("Cloning with git=%s", git_exe)

    def _do_clone() -> None:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=float(CLONE_TIMEOUT_SECONDS),
                check=False,
                env=None,
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

    await asyncio.to_thread(_do_clone)
    logger.info("Clone completed: %s -> %s", clone_url, dest)


async def cleanup_stale_workspaces(
    max_age_seconds: int = 3600, active_job_ids: set[str] | None = None
) -> int:
    """Remove archguard-* temp directories older than *max_age_seconds*.

    Called on application startup to clean up after crashes, and periodically.

    Age is measured from mtime, not atime: access times are unreliable as a
    liveness signal because they are disabled by default on Windows NTFS and on
    Linux ``noatime`` mounts, and only coarsely updated under ``relatime``. On
    such filesystems an atime-based sweep would reap a workspace a user is
    actively viewing. Liveness comes from *active_job_ids* instead, which the
    caller reads from the job manager -- an authoritative signal rather than a
    filesystem-dependent guess.

    Args:
        max_age_seconds: delete workspaces older than this.
        active_job_ids:  job IDs still known to the job manager; their
                         workspaces are never removed regardless of age.

    Returns the number of directories removed.
    """
    # The glob, the stat per candidate and the rmtree are all blocking, and
    # this runs on a 15-minute timer inside the event loop.
    return await asyncio.to_thread(
        _sweep_stale_workspaces, max_age_seconds, active_job_ids or set()
    )


def _sweep_stale_workspaces(max_age_seconds: int, active: set[str]) -> int:
    import time

    tmp = Path(tempfile.gettempdir())
    removed = 0
    now = time.time()

    for candidate in tmp.glob("archguard-*"):
        # Directory name is archguard-<job_id> for job-scoped workspaces.
        job_id = candidate.name[len("archguard-"):]
        if job_id in active:
            continue
        try:
            if not candidate.is_dir():
                continue
            age = now - candidate.stat().st_mtime
        except OSError:
            # Another sweep, or the job itself, removed it between the glob and
            # the stat. Nothing to do, and nothing worth logging.
            continue
        if age > max_age_seconds:
            shutil.rmtree(candidate, ignore_errors=True)
            # DEBUG, not INFO: the callers already log the count, and this fires
            # once per directory. A first start after a busy period swept 79 of
            # them, which was invisible only because archguard.* INFO records
            # were being discarded entirely; now that they are not, one line per
            # removal would bury the rest of startup.
            logger.debug("Removed stale workspace %s (age: %ds)", candidate, int(age))
            removed += 1

    return removed
