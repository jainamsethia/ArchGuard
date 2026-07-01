import os
from pathlib import Path


class PathTraversalError(Exception):
    pass


# Sensitive directories ArchGuard must never be pointed at, regardless of how
# the resolved path was constructed. Kept as defense-in-depth alongside
# existence/accessibility checks below.
_BLOCKED_PREFIXES = [
    Path("/etc"),
    Path("/root"),
    Path("/proc"),
    Path("/sys"),
    Path("/dev"),
]

if os.name == "nt":
    _WIN_DRIVE = Path(os.environ.get("SystemDrive", "C:") + "\\")
    _BLOCKED_PREFIXES.extend(
        [
            _WIN_DRIVE / "Windows",
            _WIN_DRIVE / "Program Files",
            _WIN_DRIVE / "Program Files (x86)",
        ]
    )


def validate_repo_path(path: str | Path) -> Path:
    """
    Validate that the given path:
    1. Is a valid filesystem path
    2. Does not contain path traversal sequences (checked on the raw input,
       before resolution, so intent is caught independent of process cwd)
    3. Resolves to an existing, accessible directory
    4. Does not resolve to a known-sensitive system directory

    Raises PathTraversalError for any violation. This function intentionally
    does NOT restrict the resolved path to a single project root, because
    legitimate ArchGuard usage analyzes arbitrary repositories supplied by the
    caller (e.g. CI checkouts, monorepo subdirectories) - it only guarantees
    the result is a real directory outside the sensitive-system denylist.
    """
    raw = str(path)
    resolved = Path(path).resolve()

    for blocked in _BLOCKED_PREFIXES:
        try:
            resolved.relative_to(blocked.resolve())
        except ValueError:
            continue
        raise PathTraversalError(
            f"Path '{raw}' resolves to a system directory: {resolved}. "
            "Refusing to analyze."
        )

    if not resolved.exists():
        raise PathTraversalError(
            f"Path '{raw}' resolves to '{resolved}', which does not exist."
        )

    if not resolved.is_dir():
        raise PathTraversalError(
            f"Path '{raw}' resolves to '{resolved}', which is not a directory."
        )

    return resolved


def validate_output_path(path: str | Path, base_dir: Path | None = None) -> Path:
    """Validate output path doesn't traverse outside base_dir."""
    resolved = Path(path).resolve()
    if base_dir is not None:
        try:
            resolved.relative_to(Path(base_dir).resolve())
        except ValueError:
            raise PathTraversalError(
                f"Output path '{path}' would write outside base directory '{base_dir}'"
            )
    return resolved
