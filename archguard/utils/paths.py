"""Shared path utilities for module boundary checking."""

import contextlib
from pathlib import Path

#: Directory names that are never part of the analysed codebase: vendored
#: dependencies, virtualenvs, VCS metadata, and build output. Every walk over a
#: repository must use this same set -- the parser and the init wizard each
#: carried their own divergent copy, so a repository with a ``build/`` or
#: ``.tox/`` directory was measured differently depending on which entered.
SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".venv",
        "venv",
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "build",
        "dist",
        "node_modules",
        "site-packages",
    }
)


def is_vendored(path: str | Path, root: str | Path | None = None) -> bool:
    """Return True if *path* lies inside a directory ArchGuard never analyses.

    When *root* is given, only the part of the path below it is considered, so
    a repository that itself lives under e.g. ``.../build/`` is not skipped
    wholesale.
    """
    p = Path(path)
    if root is not None:
        with contextlib.suppress(ValueError):
            p = p.relative_to(root)
    return any(part in SKIP_DIRS for part in p.parts)


def normalize_path(path: str | Path) -> str:
    """Return a normalized, forward-slash path string."""
    return str(Path(path)).replace("\\", "/")


def path_belongs_to_module(file_path: str | Path, module_paths: list[str]) -> bool:
    """
    Return True if file_path is under any of the module's declared paths.
    Uses proper prefix matching (not simple startswith) to avoid
    matching 'src/payments_v2' when module path is 'src/payments/'.
    """
    normalized = normalize_path(file_path)
    for module_path in module_paths:
        mp = normalize_path(module_path)
        if not mp.endswith("/"):
            mp += "/"
        if normalized.startswith(mp):
            return True
    return False
