"""Guards against ad hoc scripts and run output re-accumulating at the repo root.

This happened repeatedly across prior audit sessions. The check is defined
against *git's* view of the tree rather than a hand-maintained allowlist: a
file that .gitignore already excludes is by definition not part of the
repository, and enumerating those was a losing game -- every editor, agent, or
tool that dropped a dotfile at the root broke this test until someone added
another literal to the list.
"""

import pathlib
import subprocess

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Root-level files that are legitimately committed. Anything else that git
#: tracks or would track (i.e. is not ignored) at the root is a regression.
_ALLOWED_ROOT_FILES = {
    ".archguard.yml",
    ".dockerignore",
    ".env.example",
    ".gitattributes",
    ".gitignore",
    ".pre-commit-config.yaml",
    ".shellcheckrc",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "Dockerfile",
    # Alembic resolves its config relative to the directory it is invoked from
    # and looks for this name; it is not relocatable under docs/ or scripts/.
    "alembic.ini",
    "LICENSE",
    "Makefile",
    "README.md",
    "SECURITY.md",
    "docker-compose.yml",
    "package-lock.json",
    "package.json",
    "playwright.config.ts",
    "poetry.lock",
    "pyproject.toml",
    "railway.toml",
    "render.yaml",
}


def _git_visible_root_files() -> set[str]:
    """Root-level files git tracks or would track (ignored files excluded)."""
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", ":(top)*"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return {
        line for line in proc.stdout.splitlines() if line and "/" not in line
    }


def test_repository_root_has_no_unexpected_files():
    try:
        actual = _git_visible_root_files()
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"git unavailable: {exc}")

    unexpected = actual - _ALLOWED_ROOT_FILES
    assert not unexpected, (
        f"Unexpected files at repo root: {sorted(unexpected)}. "
        "Either delete them, move them under scripts/ or docs/, or add them to "
        ".gitignore if they are local-only."
    )


def test_allowlist_has_no_stale_entries():
    """Every allowlisted file must still exist, so the list cannot rot."""
    missing = {name for name in _ALLOWED_ROOT_FILES if not (_REPO_ROOT / name).is_file()}
    assert not missing, f"Allowlisted root files that no longer exist: {sorted(missing)}"
