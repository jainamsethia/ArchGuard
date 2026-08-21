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


#: Trees that are not the architecture under test, keyed by how they are
#: matched. Test directories are excluded at any depth, which is what
#: ``_assign_file_to_module`` already does when it skips ``/tests/`` -- so a
#: generated ``tests`` module was being measured by a scorer that elsewhere
#: refuses to assign test files at all.
#:
#: The rest are matched at the repository root only. A package may legitimately
#: contain ``scripts/`` or ``docs/`` of its own; a top-level ``docs/`` is
#: documentation for the project, not part of it.
TEST_DIR_NAMES = frozenset({"test", "tests", "testing"})
NON_SHIPPING_ROOTS = frozenset(
    {"docs", "doc", "examples", "example", "scripts", "benchmarks", "bench"}
)


def is_shipping_file(relative_path: str) -> bool:
    """Whether a repo-relative path is part of the architecture being measured.

    Test code imports broadly by design, so a coupling budget on it measures a
    property nobody intends to hold. Measured on pallets/flask, the fan-out of
    its own test package was the finding that produced an F on a project whose
    every module scored 85-100.
    """
    parts = [p for p in relative_path.replace("\\", "/").split("/") if p]
    if not parts:
        return False
    if any(part in TEST_DIR_NAMES for part in parts[:-1]):
        return False
    return parts[0] not in NON_SHIPPING_ROOTS


def path_belongs_to_module(file_path: str | Path, module_paths: list[str]) -> bool:
    """
    Return True if file_path is under any of the module's declared paths.
    Uses proper prefix matching (not simple startswith) to avoid
    matching 'src/payments_v2' when module path is 'src/payments/'.

    A path of ``.``, ``./`` or ``""`` declares the whole repository as one
    module, and means *everything that ships*: every file except the
    non-shipping trees above. Prefix matching cannot express that -- ``"./"``
    normalises to ``"."``, gains a slash, and then requires the file to start
    with ``"./"``, which no repo-relative path does. So a module declared at
    ``./`` matched nothing at all, in every layer, and was scored against zero
    files while reporting a confident grade.

    Non-shipping trees are excluded from the wildcard rather than swept in with
    everything else, so a flat repository's fan-out counts its real
    dependencies instead of pytest and sphinx. The exclusion applies only to
    the wildcard: an explicit path means exactly what it says, so a
    hand-written ``tests`` module still receives test files because its author
    asked for them by name.
    """
    normalized = normalize_path(file_path)
    for module_path in module_paths:
        mp = normalize_path(module_path)
        if mp in (".", ""):
            if is_shipping_file(normalized):
                return True
            continue
        if not mp.endswith("/"):
            mp += "/"
        if normalized.startswith(mp):
            return True
    return False
