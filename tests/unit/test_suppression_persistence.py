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
