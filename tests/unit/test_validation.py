import os

import pytest

from archguard.utils.validation import (
    PathTraversalError,
    validate_output_path,
    validate_repo_path,
)


def test_path_traversal_blocked():
    blocked_path = "/etc/passwd"
    if os.name == "nt":
        blocked_path = os.environ.get("SystemDrive", "C:") + "\\Windows\\System32"
    with pytest.raises(PathTraversalError):
        validate_repo_path(blocked_path)


def test_path_traversal_dot_dot_blocked():
    blocked_path = "../../../etc/shadow"
    if os.name == "nt":
        system_drive = os.environ.get("SystemDrive", "C:")
        blocked_path = f"{system_drive}\\temp\\..\\Windows\\System32"
    with pytest.raises(PathTraversalError):
        validate_repo_path(blocked_path)


def test_valid_path_passes(tmp_path):
    result = validate_repo_path(str(tmp_path))
    assert result == tmp_path.resolve()


def test_validate_output_path_blocked(tmp_path):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    outside_dir = tmp_path / "outside"
    with pytest.raises(PathTraversalError):
        validate_output_path(outside_dir, base_dir)


def test_validate_output_path_passes(tmp_path):
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    inside_dir = base_dir / "inside"
    result = validate_output_path(inside_dir, base_dir)
    assert result == inside_dir.resolve()


def test_valid_path_as_string() -> None:
    from archguard.contract.validator import validate_contract

    data = {
        "version": "3.0",
        "modules": [{"name": "core", "path": "src/"}],
    }
    validate_contract(data)


def test_valid_path_as_list() -> None:
    from archguard.contract.validator import validate_contract

    data = {
        "version": "3.0",
        "modules": [{"name": "core", "path": ["src/"]}],
    }
    validate_contract(data)


def test_path_traversal_dot_dot_blocked_is_cwd_independent(tmp_path, monkeypatch):
    """Regression test for the original failure: the same traversal string must
    be rejected no matter how deep the process cwd is, because /etc/shadow is a
    file, not a directory, not because of prefix-matching luck."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(PathTraversalError):
        validate_repo_path("../../../etc/shadow")


def test_nonexistent_path_blocked(tmp_path):
    missing = tmp_path / "does" / "not" / "exist"
    with pytest.raises(PathTraversalError):
        validate_repo_path(missing)


def test_file_path_blocked(tmp_path):
    a_file = tmp_path / "not_a_dir.txt"
    a_file.write_text("x")
    with pytest.raises(PathTraversalError):
        validate_repo_path(a_file)
