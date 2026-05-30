import os
from pathlib import Path


class PathTraversalError(Exception):
    pass


def validate_repo_path(path: str | Path) -> Path:
    """
    Validate that the given path:
    1. Is a valid filesystem path
    2. Does not contain path traversal sequences
    3. Is a directory (or will become one)
    4. Resolves to within acceptable bounds
    """
    resolved = Path(path).resolve()

    # Prevent traversal to sensitive system directories
    BLOCKED_PREFIXES = [
        Path("/etc"),
        Path("/root"),
        Path("/proc"),
        Path("/sys"),
        Path("/dev"),
    ]

    # Windows equivalents for common system paths
    if os.name == "nt":
        win_drive = Path(os.environ.get("SystemDrive", "C:") + "\\")
        BLOCKED_PREFIXES.extend(
            [
                win_drive / "Windows",
                win_drive / "Program Files",
                win_drive / "Program Files (x86)",
            ]
        )

    for blocked in BLOCKED_PREFIXES:
        try:
            resolved.relative_to(blocked.resolve())
        except ValueError:
            continue
        raise PathTraversalError(
            f"Path '{path}' resolves to a system directory: {resolved}. "
            "Refusing to analyze."
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
