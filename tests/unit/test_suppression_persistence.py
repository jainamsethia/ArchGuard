"""Tests for the analysis-time suppression filter.

The bug these pin: the dashboard wrote suppressions to one place while the
analysis-time filter read another, so "Suppress" never affected what the next
scan reported. Storage moved to PostgreSQL, and the filter now compares the
hashes it is handed -- so what these guard is that it uses them, and only them,
rather than going looking in the analysed clone again.

Storage itself is covered in tests/integration/test_suppression_store_db.py.
"""

from __future__ import annotations

from archguard.analysis._suppression_filter import _filter_suppressed


class _V:
    """Minimal stand-in for ViolationDetail."""

    def __init__(self, module: str, layer: int, message: str):
        self.module = module
        self.layer = layer
        self.message = message


def _hash(module: str, layer: int, message: str) -> str:
    from archguard.suppression.models import make_violation_hash

    return make_violation_hash(module, layer, message)


def test_filter_uses_the_hashes_it_is_given_not_the_analysed_tree(tmp_path):
    """The regression: a user's suppressions are not inside the analysed clone.

    They are rows in PostgreSQL owned by whoever submitted the job, resolved
    before the pipeline starts. The clone is a throwaway that has never held
    anybody's suppressions, and this test fails if the filter goes looking in it
    again.
    """
    clone = tmp_path / "throwaway-clone"
    clone.mkdir()

    violations = [
        _V("lib", 2, "fan_out=22 exceeds budget=10"),
        _V("tests", 2, "fan_out=14 exceeds budget=10"),
    ]

    # Given nothing, nothing is filtered -- including by reading the clone.
    assert len(_filter_suppressed(clone, violations)) == 2

    kept = _filter_suppressed(
        clone,
        violations,
        suppressed_hashes={_hash("lib", 2, "fan_out=22 exceeds budget=10")},
    )
    assert [v.module for v in kept] == ["tests"]


def test_filter_survives_having_no_suppressions(tmp_path):
    """A repository nobody has suppressed anything in must analyse normally."""
    violations = [_V("lib", 2, "fan_out=22 exceeds budget=10")]

    assert len(_filter_suppressed(tmp_path, violations, suppressed_hashes=None)) == 1
    assert len(_filter_suppressed(tmp_path, violations, suppressed_hashes=set())) == 1


def test_a_suppression_hash_does_not_depend_on_the_clone_or_the_job(tmp_path):
    """What makes a suppression survive a re-scan, now that storage is durable.

    Every scan gets a fresh clone and a new job id. Matching keys on the
    violation's own identity and on nothing else, so the same finding in scan 3
    hashes to what the user suppressed during scan 1.
    """
    violations = [
        _V("lib", 2, "fan_out=22 exceeds budget=10"),
        _V("tests", 2, "fan_out=14 exceeds budget=10"),
    ]
    suppressed = {_hash("lib", 2, "fan_out=22 exceeds budget=10")}

    def scan(clone_dir: str) -> list[str]:
        clone = tmp_path / clone_dir
        clone.mkdir()
        return [
            v.module
            for v in _filter_suppressed(clone, violations, suppressed_hashes=suppressed)
        ]

    assert scan("clone-1") == ["tests"]
    assert scan("clone-2") == ["tests"]
    assert scan("clone-3") == ["tests"]


def test_a_changed_message_is_a_different_finding(tmp_path):
    """Suppressing "fan_out=22" must not silently cover "fan_out=45" later."""
    suppressed = {_hash("lib", 2, "fan_out=22 exceeds budget=10")}
    worse = [_V("lib", 2, "fan_out=45 exceeds budget=10")]

    assert len(_filter_suppressed(tmp_path, worse, suppressed_hashes=suppressed)) == 1
