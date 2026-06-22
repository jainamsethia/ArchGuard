"""Unit tests for archguard.utils.monorepo.detect_subpackages."""

from __future__ import annotations

from pathlib import Path

from archguard.utils.monorepo import detect_subpackages


def test_detects_direct_subpackage(tmp_path: Path) -> None:
    (tmp_path / "service-a").mkdir()
    (tmp_path / "service-a" / "pyproject.toml").write_text("")
    result = detect_subpackages(tmp_path)
    assert tmp_path / "service-a" in result


def test_root_itself_is_excluded(tmp_path: Path) -> None:
    """The root directory having its own marker must not count as a
    'sub'-package."""
    (tmp_path / "pyproject.toml").write_text("")
    result = detect_subpackages(tmp_path)
    assert tmp_path not in result


def test_no_duplicates_across_multiple_markers(tmp_path: Path) -> None:
    """A package directory with BOTH pyproject.toml and setup.cfg must
    appear only once in the result, not once per marker file matched."""
    pkg = tmp_path / "service-b"
    pkg.mkdir()
    (pkg / "pyproject.toml").write_text("")
    (pkg / "setup.cfg").write_text("")
    result = detect_subpackages(tmp_path)
    assert result.count(pkg) == 1


def test_respects_max_depth(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "pyproject.toml").write_text("")
    result = detect_subpackages(tmp_path, max_depth=2)
    assert deep not in result  # 3 levels deep, max_depth=2 should not reach it


def test_no_subpackages_returns_empty_list(tmp_path: Path) -> None:
    assert detect_subpackages(tmp_path) == []
