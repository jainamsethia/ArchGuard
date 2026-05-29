"""Unit tests for incremental analysis caching."""

import json
from pathlib import Path
from archguard.cache.incremental import (
    FileRecord,
    compute_hash,
    get_changed_files,
    load_cache,
    save_cache,
    INCREMENTAL_CACHE_FILE
)

def test_get_changed_files_empty_cache(tmp_path: Path) -> None:
    """If cache is empty, all files are considered changed."""
    f1 = tmp_path / "a.py"
    f1.write_text("print('hello')")
    
    changed, unchanged = get_changed_files([f1], tmp_path)
    
    assert len(changed) == 1
    assert changed[0] == f1
    assert len(unchanged) == 0

def test_get_changed_files_unchanged(tmp_path: Path) -> None:
    """If file hash matches cache, it is considered unchanged."""
    f1 = tmp_path / "a.py"
    f1.write_text("print('hello')")
    
    cache_records = {
        "a.py": FileRecord(
            path="a.py",
            sha256=compute_hash(f1),
            last_analyzed="2023-01-01T00:00:00Z"
        )
    }
    save_cache(tmp_path, cache_records)
    
    changed, unchanged = get_changed_files([f1], tmp_path)
    
    assert len(changed) == 0
    assert len(unchanged) == 1
    assert unchanged[0] == f1

def test_get_changed_files_modified(tmp_path: Path) -> None:
    """If file hash does not match, it is considered changed."""
    f1 = tmp_path / "a.py"
    f1.write_text("print('hello')")
    
    # Save a fake hash
    cache_records = {
        "a.py": FileRecord(
            path="a.py",
            sha256="fake_hash_123",
            last_analyzed="2023-01-01T00:00:00Z"
        )
    }
    save_cache(tmp_path, cache_records)
    
    changed, unchanged = get_changed_files([f1], tmp_path)
    
    assert len(changed) == 1
    assert changed[0] == f1
    assert len(unchanged) == 0

def test_save_and_load_cache(tmp_path: Path) -> None:
    """Verify cache is correctly updated and read."""
    records = {
        "b.py": FileRecord(path="b.py", sha256="abcdef", last_analyzed="2023-01-01T00:00:00Z")
    }
    save_cache(tmp_path, records)
    
    loaded = load_cache(tmp_path)
    assert "b.py" in loaded
    assert loaded["b.py"].sha256 == "abcdef"
    assert loaded["b.py"].path == "b.py"

def test_save_cache_atomic(tmp_path: Path, monkeypatch) -> None:
    """Simulate crash during write — cache file should not be corrupted."""
    import os
    import pytest
    cache_file = tmp_path / INCREMENTAL_CACHE_FILE
    cache_file.write_text('{"old": {"path": "old", "sha256": "123", "last_analyzed": "0"}}')
    write_count = 0
    original_fdopen = os.fdopen
    def crashing_fdopen(*args, **kwargs):
        nonlocal write_count
        write_count += 1
        if write_count == 1:
            raise OSError("Simulated disk full")
        return original_fdopen(*args, **kwargs)
    monkeypatch.setattr(os, "fdopen", crashing_fdopen)
    with pytest.raises(OSError):
        save_cache(tmp_path, {"new": FileRecord(path="new", sha256="456", last_analyzed="1")})
    # Old cache must still be intact
    assert json.loads(cache_file.read_text()) == {"old": {"path": "old", "sha256": "123", "last_analyzed": "0"}}
