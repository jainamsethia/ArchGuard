from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _force_writable_and_retry(func: Callable[[str], Any], path: str, exc: BaseException) -> None:
    """rmtree error handler: clear the read-only bit git sets on object files.

    Git marks objects in .git read-only, which makes rmtree fail on Windows.
    Anything that is *not* a permission problem is re-raised so real failures
    (a locked file, a vanished mount) still surface.
    """
    if os.access(path, os.W_OK):
        raise exc
    os.chmod(path, stat.S_IWUSR)
    func(path)


def force_rmtree(target: Path) -> None:
    """shutil.rmtree that survives git's read-only object files."""
    if sys.version_info >= (3, 12):
        # onerror= is deprecated from 3.12 and slated for removal; onexc gets
        # the exception itself rather than a sys.exc_info() triple.
        shutil.rmtree(target, onexc=_force_writable_and_retry)
    else:
        shutil.rmtree(
            target,
            onerror=lambda f, p, info: _force_writable_and_retry(f, p, info[1]),
        )


class GitWorktreeManager:
    """Manages temporary Git worktrees for isolated historical analysis.

    Ensures the main working tree is never modified and supports concurrent
    evaluations of the same or different commits safely.
    """

    def __init__(
        self, repo_path: Path | str, base_worktree_dir: Path | str | None = None
    ) -> None:
        self.repo_path = Path(repo_path).resolve()

        if base_worktree_dir is None:
            self.base_dir = self.repo_path / ".archguard-cache" / "worktrees"
        else:
            self.base_dir = Path(base_worktree_dir).resolve()

        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._prune_stale_metadata()

    def _run_git(self, *args: str) -> subprocess.CompletedProcess[str]:
        cmd = ["git", "-C", str(self.repo_path), *args]
        return subprocess.run(cmd, capture_output=True, text=True, check=True)

    def _prune_stale_metadata(self) -> None:
        """Prune git worktree metadata for deleted directories."""
        try:
            self._run_git("worktree", "prune")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to prune git worktrees: {e.stderr}")

    def create_worktree(self, commit_ish: str) -> Path:
        """Create a detached worktree for a specific commit or branch."""
        wt_id = uuid.uuid4().hex
        wt_path = self.base_dir / wt_id

        try:
            # Create detached head to avoid branch conflicts
            self._run_git("worktree", "add", "-f", "--detach", str(wt_path), commit_ish)
            return wt_path
        except subprocess.CalledProcessError as e:
            logger.exception(f"Failed to create worktree for {commit_ish}: {e.stderr}")
            if wt_path.exists():
                shutil.rmtree(wt_path, ignore_errors=True)
            raise RuntimeError(f"Could not create worktree: {e.stderr}") from e

    def cleanup_worktree(self, wt_path: Path) -> bool:
        """Remove the worktree and clean up git metadata safely."""
        wt_path = Path(wt_path).resolve()
        success = True

        try:
            self._run_git("worktree", "remove", "-f", str(wt_path))
        except subprocess.CalledProcessError as e:
            logger.warning(f"git worktree remove failed for {wt_path}: {e.stderr}")
            success = False

        # Fallback manual deletion if Git fails or the folder is somehow left behind
        if wt_path.exists():
            try:
                force_rmtree(wt_path)
            except Exception as e:
                logger.warning(
                    f"Failed to manually remove worktree directory {wt_path}: {e}"
                )
                success = False

        # Always prune to ensure metadata is clean
        self._prune_stale_metadata()
        return success

    @contextmanager
    def checkout(self, commit_ish: str) -> Iterator[Path]:
        """Context manager for safely executing within a historical worktree."""
        wt_path = self.create_worktree(commit_ish)
        try:
            yield wt_path
        finally:
            self.cleanup_worktree(wt_path)


@contextmanager
def git_worktree(repo_root: Path | str, sha: str) -> Iterator[Path]:
    repo_root = Path(repo_root).resolve()
    wt_id = uuid.uuid4().hex
    tmp_dir = Path(tempfile.mkdtemp(prefix="archguard_wt_"))
    wt_path = tmp_dir / wt_id
    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt_path), sha],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
        )
        yield wt_path
    finally:
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt_path)],
                cwd=str(repo_root),
                check=False,
                capture_output=True,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("Failed to remove git worktree %s: %s", wt_path, exc)
        if tmp_dir.exists():
            try:
                force_rmtree(tmp_dir)
            except OSError as exc:
                # Leaving a temp dir behind must not mask the caller's own
                # exception on the way out of the context manager.
                logger.warning("Could not remove worktree temp dir %s: %s", tmp_dir, exc)
