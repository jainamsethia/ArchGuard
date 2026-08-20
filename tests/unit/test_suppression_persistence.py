"""Tests for suppression persistence across dashboard re-scans.

The bug these pin: the dashboard stored suppressions under
``.archguard-cache/suppressions-{job_id}.jsonl`` while the analysis-time filter
looked inside the analysed tree. Both halves were wrong for the dashboard's
model -- it clones a throwaway workspace per scan, and every scan gets a new job
id -- so "Suppress" never affected what the next scan reported.
"""

from __future__ import annotations

import pytest

from archguard.analysis._suppression_filter import _filter_suppressed
from archguard.suppression.scope import repo_slug, suppression_path_for_repo
from archguard.suppression.store import SuppressionStore


class _V:
    """Minimal stand-in for ViolationDetail."""

    def __init__(self, module: str, layer: int, message: str):
        self.module = module
        self.layer = layer
        self.message = message


# ---------------------------------------------------------------------------
# Keying: one repository -> one store, however its URL is spelled
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/sqlmapproject/sqlmap",
        "https://github.com/sqlmapproject/sqlmap.git",
        "https://github.com/sqlmapproject/sqlmap/",
        "git@github.com:sqlmapproject/sqlmap.git",
        "https://github.com/SqlmapProject/SqlMap",
    ],
)
def test_url_spellings_of_one_repo_share_a_store(url):
    """A re-scan must find last scan's suppressions even if the user pasted the
    URL differently the second time."""
    assert repo_slug(url) == "sqlmapproject__sqlmap"


def test_different_repos_do_not_share_a_store():
    a = repo_slug("https://github.com/owner/alpha")
    b = repo_slug("https://github.com/owner/beta")
    c = repo_slug("https://github.com/other/alpha")
    assert len({a, b, c}) == 3


def test_slug_is_a_single_safe_path_segment():
    slug = repo_slug("https://github.com/owner/repo")
    assert "/" not in slug and "\\" not in slug
    assert ".." not in slug


def test_unparseable_url_gets_a_stable_hashed_slug():
    """Better a stable private bucket than collapsing unknown repos together."""
    weird = "not-a-github-url"
    first, second = repo_slug(weird), repo_slug(weird)

    assert first == second
    assert first.startswith("url-")
    assert first != repo_slug("also-not-a-url")


def test_path_is_scoped_under_the_base_directory(tmp_path):
    path = suppression_path_for_repo(tmp_path, "https://github.com/owner/repo")

    assert path.parent.parent == tmp_path
    assert path.name == "owner__repo.jsonl"
    # Never escapes the base, even for a hostile-looking URL.
    hostile = suppression_path_for_repo(tmp_path, "https://github.com/../../etc/passwd")
    assert tmp_path in hostile.parents


# ---------------------------------------------------------------------------
# The analysis-time filter honours an explicit store
# ---------------------------------------------------------------------------


def test_filter_uses_the_explicit_store_not_the_analysed_tree(tmp_path):
    """The regression: the dashboard's store lives outside the analysed clone."""
    clone = tmp_path / "throwaway-clone"
    clone.mkdir()
    durable = tmp_path / "durable" / "owner__repo.jsonl"
    durable.parent.mkdir(parents=True)

    SuppressionStore.at_path(durable).add(
        module="lib", layer=2, message="fan_out=22 exceeds budget=10",
        reason="accepted debt",
    )

    violations = [
        _V("lib", 2, "fan_out=22 exceeds budget=10"),
        _V("tests", 2, "fan_out=14 exceeds budget=10"),
    ]

    # Without the explicit store the clone has nothing, so nothing is filtered.
    assert len(_filter_suppressed(clone, violations)) == 2

    kept = _filter_suppressed(clone, violations, store_path=durable)
    assert [v.module for v in kept] == ["tests"]


def test_filter_survives_a_missing_store_file(tmp_path):
    """A repo with no suppressions yet must analyse normally."""
    violations = [_V("lib", 2, "fan_out=22 exceeds budget=10")]
    missing = tmp_path / "never-written" / "owner__repo.jsonl"

    assert len(_filter_suppressed(tmp_path, violations, store_path=missing)) == 1


def test_suppression_written_once_applies_to_every_later_scan(tmp_path):
    """End-to-end shape of the fix, without running the analyser.

    Scan 1 sees the violation, the user suppresses it, and scans 2 and 3 -- each
    of which would have had its own job id -- no longer report it.
    """
    base = tmp_path / "cache"
    url = "https://github.com/owner/repo"
    violations = [
        _V("lib", 2, "fan_out=22 exceeds budget=10"),
        _V("tests", 2, "fan_out=14 exceeds budget=10"),
    ]

    def scan(clone_dir: str) -> list[str]:
        # A different throwaway clone each time, as a real re-scan would have.
        clone = tmp_path / clone_dir
        clone.mkdir()
        path = suppression_path_for_repo(base, url)
        return [v.module for v in _filter_suppressed(clone, violations, store_path=path)]

    assert scan("clone-1") == ["lib", "tests"]

    path = suppression_path_for_repo(base, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    SuppressionStore.at_path(path).add(
        module="lib", layer=2, message="fan_out=22 exceeds budget=10",
        reason="accepted debt",
    )

    assert scan("clone-2") == ["tests"]
    assert scan("clone-3") == ["tests"]


def test_expired_suppression_stops_hiding_the_violation(tmp_path):
    """Suppression persistence must not mean permanence."""
    path = tmp_path / "owner__repo.jsonl"
    SuppressionStore.at_path(path).add(
        module="lib", layer=2, message="fan_out=22 exceeds budget=10",
        reason="temporary", expires_at="2020-01-01T00:00:00+00:00",
    )

    violations = [_V("lib", 2, "fan_out=22 exceeds budget=10")]
    assert len(_filter_suppressed(tmp_path, violations, store_path=path)) == 1


# ---------------------------------------------------------------------------
# Store construction
# ---------------------------------------------------------------------------


def test_at_path_backs_the_store_with_the_given_file(tmp_path):
    path = tmp_path / "nested" / "store.jsonl"
    path.parent.mkdir(parents=True)
    store = SuppressionStore.at_path(path)

    store.add(module="m", layer=2, message="msg", reason="r")

    assert path.exists()
    assert store.is_suppressed("m", 2, "msg") is True


def test_repo_root_constructor_still_uses_the_checkout(tmp_path):
    """The CLI's keying is unchanged: suppressions live inside the checkout."""
    from archguard.config import SUPPRESSION_FILE

    store = SuppressionStore(tmp_path)
    store.add(module="m", layer=2, message="msg", reason="r")

    assert (tmp_path / SUPPRESSION_FILE).exists()
