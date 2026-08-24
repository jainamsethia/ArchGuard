"""Async workspace management for cloned repositories.

Provides an async context manager that:
1. Creates a temp directory prefixed with 'archguard-'
2. Blobless-clones the target repository into it, retaining full commit history
3. Yields the Path to the cloned repo root
4. Guarantees cleanup (shutil.rmtree) in try/finally regardless of exceptions

The clone is deliberately *not* shallow.  Contract generation derives module
boundaries from co-change history (see ``archguard.contract._discovery``), and a
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

#: Ceiling on the total bytes held by all analysis workspaces. Default 5 GiB.
#:
#: The age sweep below does not bound disk on its own: it exempts every job the
#: manager still knows about, up to MAX_STORED_JOBS (50), so fifty completed
#: jobs holding large clones sit on disk indefinitely. This is the ceiling that
#: actually binds. Set to 0 to disable it.
MAX_WORKSPACE_BYTES: int = int(
    os.environ.get("ARCHGUARD_MAX_WORKSPACE_BYTES", str(5 * 1024**3))
)


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


def _dir_size_bytes(path: Path) -> int:
    """Bytes held under *path*, tolerating entries that vanish mid-walk."""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path, onerror=lambda _exc: None):
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                # A concurrent sweep, or the owning job, removed it. Not worth
                # logging once per file.
                continue
    return total


def _sweep_to_budget(max_bytes: int, active: set[str]) -> tuple[int, int]:
    """Evict oldest evictable workspaces until the total fits *max_bytes*.

    Returns ``(evicted_count, bytes_reclaimed)``. Blocking; call in a thread.

    *active* should hold only the jobs that are still **running**, not every job
    the manager remembers. A finished job's clone is a cache for browsing
    results -- every read endpoint already falls back to the persisted run when
    the workspace is gone -- so under real disk pressure it is the right thing
    to give up. Protecting all remembered jobs, the way the age sweep does,
    would leave nothing evictable and make this ceiling decorative.

    ponytail: walks every file under every workspace on each call, which is
    O(files on disk) once per job. Fine at a 5 GiB ceiling and one call per
    analysis; if that stops holding, record each workspace's size at creation
    and keep a running total instead.
    """
    if max_bytes <= 0:
        return 0, 0

    tmp = Path(tempfile.gettempdir())
    total = 0
    evictable: list[tuple[float, Path, int]] = []

    for candidate in tmp.glob("archguard-*"):
        try:
            if not candidate.is_dir():
                continue
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        size = _dir_size_bytes(candidate)
        total += size
        if candidate.name[len("archguard-") :] not in active:
            evictable.append((mtime, candidate, size))

    if total <= max_bytes:
        return 0, 0

    evictable.sort(key=lambda entry: entry[0])  # oldest first

    evicted = 0
    reclaimed = 0
    for _mtime, path, size in evictable:
        if total - reclaimed <= max_bytes:
            break
        shutil.rmtree(path, ignore_errors=True)
        reclaimed += size
        evicted += 1
        logger.info(
            "Evicted workspace %s (%d bytes) to stay within the disk budget", path, size
        )

    if total - reclaimed > max_bytes:
        logger.warning(
            "Workspace disk usage is still over budget after evicting %d workspace(s): "
            "%d bytes held against a %d byte ceiling. The remainder belongs to "
            "running jobs and cannot be reclaimed.",
            evicted,
            total - reclaimed,
            max_bytes,
        )

    return evicted, reclaimed


async def enforce_workspace_budget(
    max_bytes: int | None = None, active_job_ids: set[str] | None = None
) -> tuple[int, int]:
    """Bring total workspace disk usage under the configured ceiling.

    Returns ``(evicted_count, bytes_reclaimed)``.
    """
    return await asyncio.to_thread(
        _sweep_to_budget,
        MAX_WORKSPACE_BYTES if max_bytes is None else max_bytes,
        active_job_ids or set(),
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


#: A scheduled check must not hang the scheduler behind one unreachable remote.
#: Far shorter than the clone timeout: this is one round trip that returns a few
#: hundred bytes, not a repository.
LS_REMOTE_TIMEOUT_SECONDS: int = int(os.environ.get("ARCHGUARD_LS_REMOTE_TIMEOUT", "20"))

#: Only ever asked about repositories on this host. The URLs handed here are
#: rebuilt from validated owner/name parts by ``build_safe_clone_url``, so this
#: is a backstop rather than the primary control -- but ``git ls-remote`` will
#: happily dial any host it is given, including one inside the network, and a
#: scheduled job reaching arbitrary hosts unattended is worth refusing twice.
_ALLOWED_REMOTE_PREFIX = "https://github.com/"


async def remote_head(clone_url: str) -> str | None:
    """The commit the remote reports for HEAD, or ``None`` if it cannot be read.

    ``git ls-remote`` asks the question a clone would answer, for a few hundred
    bytes instead of a repository. That is what makes a scheduled re-scan
    affordable: ADR-009 measured re-analysing an unchanged repository at ~4s in
    a warm worker, and this is how the scheduler avoids paying it at all when
    nothing has moved.

    Never raises. A watched repository that has been deleted, renamed or made
    private must not take down the scan of every other one -- the caller
    records the failed check and moves on, which is why ``last_checked_at`` is
    written even when this returns ``None``.
    """
    if not clone_url.startswith(_ALLOWED_REMOTE_PREFIX):
        logger.warning(
            "Refusing to poll %r: only %s remotes are polled",
            clone_url, _ALLOWED_REMOTE_PREFIX,
        )
        return None

    git_exe = _find_git()
    if not git_exe:
        logger.warning("git executable not found; cannot poll remotes")
        return None

    # `--` terminates option parsing, so a URL that somehow began with a dash
    # is treated as a URL rather than as a flag.
    cmd = [os.path.abspath(git_exe), "ls-remote", "--quiet", "--", clone_url, "HEAD"]

    def _run() -> str | None:
        import subprocess

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=float(LS_REMOTE_TIMEOUT_SECONDS),
                check=False,
                env=None,
            )
        except subprocess.TimeoutExpired:
            logger.warning("ls-remote timed out for %s", clone_url)
            return None
        except OSError as exc:
            logger.warning("ls-remote could not run for %s: %s", clone_url, exc)
            return None

        if proc.returncode != 0:
            logger.info(
                "ls-remote failed for %s: %s",
                clone_url,
                proc.stderr.decode("utf-8", errors="replace").strip()[:200],
            )
            return None

        first = proc.stdout.decode("utf-8", errors="replace").split("\n", 1)[0].strip()
        sha = first.split("\t", 1)[0].strip() if first else ""
        # A 40-character hex sha, or nothing. Anything else means the output
        # was not what we think it was, and guessing would put junk in a column
        # the scheduler compares against.
        if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha.lower()):
            return sha.lower()
        logger.info("ls-remote gave no usable HEAD for %s", clone_url)
        return None

    return await asyncio.to_thread(_run)
