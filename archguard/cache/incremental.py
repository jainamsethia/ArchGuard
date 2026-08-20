"""
Incremental analysis: track file content hashes to skip unchanged files.
"""

import contextlib
import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

INCREMENTAL_CACHE_FILE = ".archguard-cache.json"


@dataclass
class FileRecord:
    path: str
    sha256: str
    last_analyzed: str  # ISO timestamp


def compute_hash(file_path: Path) -> str:
    h = hashlib.sha256()
    h.update(file_path.read_bytes())
    return h.hexdigest()


def load_cache(root: Path) -> dict[str, FileRecord]:
    cache_file = root / INCREMENTAL_CACHE_FILE
    if not cache_file.exists():
        return {}
    try:
        data = json.loads(cache_file.read_text())
        return {k: FileRecord(**v) for k, v in data.items()}
    except (json.JSONDecodeError, OSError) as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Cache file corrupted ({e}), starting fresh: {cache_file}")
        corrupt_path = cache_file.with_suffix(".corrupt")
        with contextlib.suppress(OSError):
            cache_file.rename(corrupt_path)
        return {}


def save_cache(root: Path, records: dict[str, FileRecord]) -> None:
    import os
    import tempfile

    cache_file = root / INCREMENTAL_CACHE_FILE
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_data = {k: asdict(v) for k, v in records.items()}
    fd, tmp_path = tempfile.mkstemp(
        dir=cache_file.parent, prefix=".archguard_cache_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cache_data, f, indent=2)
        os.replace(tmp_path, cache_file)
    except OSError as exc:
        logger = logging.getLogger(__name__)
        logger.warning("Failed to write cache file atomically: %s", exc)
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def get_changed_files(files: list[Path], root: Path) -> tuple[list[Path], list[Path]]:
    """Returns (changed_files, unchanged_files) based on SHA-256 comparison."""
    cache = load_cache(root)
    changed, unchanged = [], []
    for f in files:
        key = str(f.relative_to(root)).replace("\\", "/")
        current_hash = compute_hash(f)
        if key not in cache or cache[key].sha256 != current_hash:
            changed.append(f)
        else:
            unchanged.append(f)
    return changed, unchanged
