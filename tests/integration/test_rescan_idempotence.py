"""Scanning an unchanged repository twice must not change what it reports.

The obvious property, and the one incremental re-analysis was quietly breaking.
A rescan finding nothing new should persist exactly what the scan before it
persisted -- same findings, same count, same everything.

What went wrong is a seam between two reasonable decisions. When no file has
changed the plan says "nothing is dirty, so every previous finding still holds"
and offers them all to be carried forward. But the adapter cannot hand an empty
file list to the orchestrator -- that path reports a skipped run scoring 0.0/F,
which would tell a user their healthy repository had failed -- so it falls back
to the full list and re-analyses everything. Both things then happened at once:
every finding was recomputed *and* carried, and both copies were persisted.

It compounds. The next scan reads both copies back as the previous run, carries
both, adds a third, and so on. A watched repository is rescanned on a schedule
whether or not anything was pushed, so this is the ordinary case for the feature
most likely to be running unattended: violation counts climbing on a repository
nobody has touched.

These tests scan the same unchanged tree three times through the real pipeline
and assert the persisted result never moves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.db_fixtures import requires_postgres
from tests.integration._pipeline_scan import (
    identity,
    make_user,
    previous_run,
    scan_repo,
    violations_of,
)

pytestmark = pytest.mark.integration


#: Two modules that import each other, each forbidding the other and allowing
#: no coupling at all. Every scan therefore produces four findings -- a Layer 1
#: and a Layer 2 for each module -- which is what gives the rescan something to
#: duplicate. Both layers on purpose: the defect is in carry-forward
#: bookkeeping, which is layer-agnostic, so pinning it on one layer would leave
#: the other free to regress.
#:
#: Layers 3 and 4 are switched off. They are the slow ones, they need the ML
#: extras, and this test is about what the adapter persists rather than about
#: what any layer measures.
CONTRACT = """version: '3.0'
skip_layers: [semantic, duplication]
modules:
- name: alpha
  path: alpha/
  coupling_budget: 0
  disallowed_imports: [beta]
- name: beta
  path: beta/
  coupling_budget: 0
  disallowed_imports: [alpha]
"""


@pytest.fixture()
def noisy_repo(tmp_path: Path) -> Path:
    """A repository that reliably produces findings, and never changes."""
    repo = tmp_path / "source"
    (repo / "alpha").mkdir(parents=True)
    (repo / "beta").mkdir(parents=True)
    (repo / "alpha" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "beta" / "__init__.py").write_text("", encoding="utf-8")
    # Each imports the other: forbidden by the contract (Layer 1) and over a
    # budget of zero (Layer 2), so both modules have findings on every scan.
    (repo / "alpha" / "core.py").write_text(
        "import beta.api\nimport json\n", encoding="utf-8"
    )
    (repo / "beta" / "api.py").write_text(
        "import alpha.core\nimport sys\n", encoding="utf-8"
    )
    (repo / ".archguard.yml").write_text(CONTRACT, encoding="utf-8")
    return repo


@requires_postgres
def test_a_rescan_of_an_unchanged_repository_reports_the_same_thing(
    noisy_repo, tmp_path, live_db
):
    """The headline property. Nothing changed, so nothing should have changed."""
    user_id = make_user(8101, "rescan-same")
    url = "https://github.com/test/rescan-same.git"

    first = scan_repo(noisy_repo, tmp_path / "c1", url, user_id)
    before = violations_of(first["run"])
    assert before, (
        "the fixture produced no findings at all, so this test would pass "
        "however badly the rescan behaved"
    )

    second = scan_repo(
        noisy_repo, tmp_path / "c2", url, user_id,
        previous=previous_run(noisy_repo, first),
    )
    after = violations_of(second["run"])

    assert len(after) == len(before), (
        f"a rescan with nothing changed reported {len(after)} findings where "
        f"the scan before it reported {len(before)}"
    )
    assert identity(after) == identity(before)


@requires_postgres
def test_repeated_rescans_do_not_accumulate_findings(noisy_repo, tmp_path, live_db):
    """Three scans, because the defect compounds rather than merely doubling.

    Each rescan read the previous run's violations -- including the copies it
    had itself added -- carried them all, and recomputed them on top. Two scans
    show doubling; three show that it keeps going, which is what makes it
    serious for a repository being swept daily.
    """
    user_id = make_user(8102, "rescan-accumulate")
    url = "https://github.com/test/rescan-accumulate.git"

    counts = []
    scan = scan_repo(noisy_repo, tmp_path / "c1", url, user_id)
    counts.append(len(violations_of(scan["run"])))

    for n in (2, 3):
        scan = scan_repo(
            noisy_repo, tmp_path / f"c{n}", url, user_id,
            previous=previous_run(noisy_repo, scan),
        )
        counts.append(len(violations_of(scan["run"])))

    assert counts[0] > 0, "the fixture produced no findings to accumulate"
    assert len(set(counts)) == 1, (
        f"findings accumulated across three unchanged scans: {counts}"
    )


@requires_postgres
def test_nothing_is_reported_twice_within_one_rescan(noisy_repo, tmp_path, live_db):
    """Stated as the property rather than the count, so it holds whatever the
    fixture happens to produce."""
    user_id = make_user(8103, "rescan-dupes")
    url = "https://github.com/test/rescan-dupes.git"

    first = scan_repo(noisy_repo, tmp_path / "c1", url, user_id)
    second = scan_repo(
        noisy_repo, tmp_path / "c2", url, user_id,
        previous=previous_run(noisy_repo, first),
    )

    found = identity(violations_of(second["run"]))
    duplicated = [v for v in set(found) if found.count(v) > 1]
    assert duplicated == [], (
        f"the same finding was persisted more than once by one rescan: {duplicated}"
    )


@requires_postgres
def test_an_edit_still_reuses_the_untouched_module(noisy_repo, tmp_path, live_db):
    """The saving must survive the fix.

    Dropping the carry when nothing changed must not drop it when something
    did: editing alpha should still leave beta's findings in the result without
    beta being re-analysed. Otherwise the fix for double-counting would quietly
    become "never reuse anything", which is the optimisation removed.
    """
    user_id = make_user(8104, "rescan-partial")
    url = "https://github.com/test/rescan-partial.git"

    first = scan_repo(noisy_repo, tmp_path / "c1", url, user_id)
    beta_before = [v for v in violations_of(first["run"]) if v.get("module") == "beta"]
    assert beta_before, "the fixture gave beta no findings to reuse"

    # Touch alpha only.
    path = noisy_repo / "alpha" / "core.py"
    path.write_text(path.read_text(encoding="utf-8") + "import re\n", encoding="utf-8")

    second = scan_repo(
        noisy_repo, tmp_path / "c2", url, user_id,
        previous=previous_run(noisy_repo, first),
    )
    beta_after = [v for v in violations_of(second["run"]) if v.get("module") == "beta"]

    assert beta_after, "the untouched module's findings vanished from the rescan"
    assert identity(beta_after) == identity(beta_before), (
        "the untouched module's findings changed without the module changing"
    )
    duplicated = [
        v for v in set(identity(beta_after)) if identity(beta_after).count(v) > 1
    ]
    assert duplicated == [], f"carried findings were also recomputed: {duplicated}"
