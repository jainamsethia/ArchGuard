"""Shared path utilities for module boundary checking."""

from pathlib import Path


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
