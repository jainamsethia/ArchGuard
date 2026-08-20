import shutil
import subprocess
from pathlib import Path

import pytest

from archguard.evolution.worktree import GitWorktreeManager


@pytest.fixture
def dummy_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()

    def run_git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    run_git("init")

    # Configure git dummy user
    run_git("config", "user.name", "Test User")
    run_git("config", "user.email", "test@example.com")

    # Create first commit
    (repo / "file1.txt").write_text("v1")
    run_git("add", "file1.txt")
    run_git("commit", "-m", "Initial commit")

    # Create second commit
    (repo / "file1.txt").write_text("v2")
    run_git("add", "file1.txt")
    run_git("commit", "-m", "Second commit")

    return repo


def test_worktree_creation_and_cleanup(dummy_git_repo: Path):
    manager = GitWorktreeManager(dummy_git_repo)

    wt_path = manager.create_worktree("HEAD~1")
    assert wt_path.exists()
    assert (wt_path / "file1.txt").read_text() == "v1"

    # Main tree should still be v2
    assert (dummy_git_repo / "file1.txt").read_text() == "v2"

    success = manager.cleanup_worktree(wt_path)
    assert success
    assert not wt_path.exists()


def test_checkout_context_manager(dummy_git_repo: Path):
    manager = GitWorktreeManager(dummy_git_repo)

    with manager.checkout("HEAD~1") as wt_path:
        assert wt_path.exists()
        assert (wt_path / "file1.txt").read_text() == "v1"

    # Should be cleaned up automatically
    assert not wt_path.exists()


def test_concurrent_duplicate_commits(dummy_git_repo: Path):
    manager = GitWorktreeManager(dummy_git_repo)

    wt1 = manager.create_worktree("HEAD")
    wt2 = manager.create_worktree("HEAD")

    assert wt1.exists()
    assert wt2.exists()
    assert wt1 != wt2

    manager.cleanup_worktree(wt1)
    manager.cleanup_worktree(wt2)


def test_cleanup_failure_handled(dummy_git_repo: Path):
    manager = GitWorktreeManager(dummy_git_repo)
    wt_path = manager.create_worktree("HEAD")

    # Manually delete the directory to break git worktree remove
    shutil.rmtree(wt_path)

    # Cleanup should still handle it via fallback/prune
    manager.cleanup_worktree(wt_path)
    assert not wt_path.exists()
