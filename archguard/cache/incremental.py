"""
Incremental analysis: track file content hashes to skip unchanged files.
"""

import hashlib
import json
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

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
    except Exception:
        return {}

def save_cache(root: Path, records: dict[str, FileRecord]) -> None:
    cache_file = root / INCREMENTAL_CACHE_FILE
    cache_file.write_text(json.dumps({k: asdict(v) for k, v in records.items()}, indent=2))

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
