from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from archguard.config import CHECKPOINTS_DIR


def save_checkpoint(repo_root: Path, phase: int, data: dict[str, Any]) -> None:
    """Save checkpoint JSON for a completed phase."""
    cp_dir = repo_root / CHECKPOINTS_DIR
    cp_dir.mkdir(parents=True, exist_ok=True)

    checkpoint = {
        "phase": phase,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }

    path = cp_dir / f"phase_{phase}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(checkpoint, f, indent=2, default=str)


def load_checkpoint(repo_root: Path, phase: int) -> dict[str, Any] | None:
    """Load checkpoint JSON for a specific phase.  Returns None if not found."""
    path = repo_root / CHECKPOINTS_DIR / f"phase_{phase}.json"
    if not path.is_file():
        return None
    try:
        import typing

        with path.open("r", encoding="utf-8") as f:
            return typing.cast(typing.Dict[str, Any], json.load(f))
    except (json.JSONDecodeError, OSError):
        return None


def latest_completed_phase(repo_root: Path) -> int:
    """Return the highest completed phase number, or 0 if none."""
    for phase in range(5, 0, -1):
        if load_checkpoint(repo_root, phase) is not None:
            return phase
    return 0
