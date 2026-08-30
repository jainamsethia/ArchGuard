"""An incremental scan must report the same numbers as a full one.

The health score, the band and the per-module scores are what a user actually
reads. Everything else on the dashboard is detail underneath them. So the one
property that matters here is equivalence: for a given repository state, a scan
that reused work must say exactly what a scan that did not would have said.

It did not. Editing a single file in a module that had no findings at all took
the same repository from 0.0/FAIL to 100.0/PASS, with all four violations still
listed on the page below the score. The layers only ever measured the slice
they re-analysed, and the findings carried forward from everywhere else were
appended to the violation list without ever reaching the arithmetic.

That is worse than a wrong number on a page. `archguard/watch/service.py`
compares the score between consecutive runs to decide whether a watched
repository has regressed, so a score that swings on which files happened to
change is a regression detector wired to noise.

These tests state the property directly -- run it both ways, compare -- rather
than asserting particular numbers, so they keep meaning something when the
scoring weights change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.db_fixtures import requires_postgres
from tests.integration._pipeline_scan import (
    make_user,
    previous_run,
    scan_repo,
    violations_of,
)

pytestmark = pytest.mark.integration


#: alpha and beta each import the other, which the contract forbids and which
#: exceeds a coupling budget of zero -- so both carry Layer 1 and Layer 2
#: findings on every scan. gamma is clean and exists to be edited: touching it
#: dirties nothing that has anything wrong with it, which is the case that
#: exposed the defect.
#:
#: Layers 3 and 4 are switched off. They are the slow ones and they need the ML
#: extras; this is a test about arithmetic, not about what a layer measures.
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
- name: gamma
  path: gamma/
  coupling_budget: 9
"""


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """The source the scans clone from. Tests edit it between scans."""
    root = tmp_path / "source"
    for mod in ("alpha", "beta", "gamma"):
        (root / mod).mkdir(parents=True)
        (root / mod / "__init__.py").write_text("", encoding="utf-8")
    (root / "alpha" / "core.py").write_text(
        "import beta.api\nimport json\n", encoding="utf-8"
    )
    (root / "beta" / "api.py").write_text(
        "import alpha.core\nimport sys\n", encoding="utf-8"
    )
    (root / "gamma" / "util.py").write_text("import json\n", encoding="utf-8")
    (root / ".archguard.yml").write_text(CONTRACT, encoding="utf-8")
    return root


def _shape(run: dict) -> dict:
    """The numbers a user sees, as one comparable object."""
    return {
        "score": run.get("score"),
        "band": run.get("band"),
        "module_scores": run.get("module_scores") or {},
        "violations": sorted(
            (str(v.get("layer")), str(v.get("module")), str(v.get("message")))
            for v in violations_of(run)
        ),
    }


def _incremental_vs_full(repo: Path, tmp_path: Path, edit, uid_seed: int) -> tuple[dict, dict]:
    """Scan, apply *edit*, then measure the same state both ways.

    The comparison has to be against a full scan of the *edited* tree, not
    against the first scan -- otherwise a test would be asserting that an edit
    changes nothing, which is not the property. So: scan, edit, then run an
    incremental scan and an independent full scan over identical content, and
    require them to agree.
    """
    url = f"https://github.com/test/scoring-{uid_seed}.git"
    uid = make_user(uid_seed, f"scoring-{uid_seed}")

    first = scan_repo(repo, tmp_path / "c1", url, uid)
    edit(repo)

    incremental = scan_repo(
        repo, tmp_path / "c2", url, uid, previous=previous_run(repo, first)
    )
    # A fresh user and URL, so this one has no previous run and must analyse
    # everything -- the reference answer for the edited tree.
    full_uid = make_user(uid_seed + 1, f"scoring-{uid_seed}-full")
    full = scan_repo(
        repo, tmp_path / "c3", f"https://github.com/test/scoring-{uid_seed}-full.git", full_uid
    )
    return _shape(incremental["run"]), _shape(full["run"])


# ------------------------------------------------- the reproduced failure


@requires_postgres
def test_violations_entirely_in_unchanged_modules_still_count(repo, tmp_path, live_db):
    """The exact reported failure.

    Every finding lives in alpha and beta; the edit touches gamma, which has
    none. Before the fix this reported 100.0/PASS against a full scan's
    0.0/FAIL while listing all four violations.
    """
    def edit(root: Path) -> None:
        p = root / "gamma" / "util.py"
        p.write_text(p.read_text(encoding="utf-8") + "import re\n", encoding="utf-8")

    incremental, full = _incremental_vs_full(repo, tmp_path, edit, 9301)

    assert incremental["violations"] == full["violations"], "different findings"
    assert incremental["score"] == full["score"], (
        f"incremental scored {incremental['score']} where a full analysis of the "
        f"same tree scored {full['score']}"
    )
    assert incremental["band"] == full["band"]
    assert incremental["module_scores"] == full["module_scores"]


@requires_postgres
def test_the_score_cannot_contradict_the_violation_list(repo, tmp_path, live_db):
    """Stated as the property a reader would notice, independent of any number.

    A page that says PASS above a list of failing violations is wrong on its
    face, whatever the arithmetic behind it.
    """
    def edit(root: Path) -> None:
        p = root / "gamma" / "util.py"
        p.write_text(p.read_text(encoding="utf-8") + "import re\n", encoding="utf-8")

    incremental, _full = _incremental_vs_full(repo, tmp_path, edit, 9303)

    if incremental["violations"]:
        assert incremental["band"] != "PASS", (
            f"band is PASS with {len(incremental['violations'])} violations listed"
        )
        assert incremental["score"] != 100.0, (
            f"score is a perfect 100.0 with {len(incremental['violations'])} "
            "violations listed"
        )


# --------------------------------------------------- the other arrangements


@requires_postgres
def test_violations_in_the_changed_module_still_count(repo, tmp_path, live_db):
    """The case that already worked, pinned so the fix does not break it."""
    def edit(root: Path) -> None:
        p = root / "alpha" / "core.py"
        p.write_text(p.read_text(encoding="utf-8") + "import re\n", encoding="utf-8")

    incremental, full = _incremental_vs_full(repo, tmp_path, edit, 9305)

    assert incremental["violations"] == full["violations"]
    assert incremental["score"] == full["score"]
    assert incremental["band"] == full["band"]
    assert incremental["module_scores"] == full["module_scores"]


@requires_postgres
def test_a_mix_of_changed_and_unchanged_modules(repo, tmp_path, live_db):
    """One dirty module with findings, one clean module edited, one untouched
    module with findings -- all three states in a single scan."""
    def edit(root: Path) -> None:
        for rel in ("alpha/core.py", "gamma/util.py"):
            p = root / rel
            p.write_text(p.read_text(encoding="utf-8") + "import re\n", encoding="utf-8")

    incremental, full = _incremental_vs_full(repo, tmp_path, edit, 9307)

    assert incremental["violations"] == full["violations"]
    assert incremental["score"] == full["score"]
    assert incremental["band"] == full["band"]
    assert incremental["module_scores"] == full["module_scores"]


@requires_postgres
def test_a_no_change_rescan_reports_the_same_numbers(repo, tmp_path, live_db):
    """Nothing changed, so nothing about the result may change either.

    Complements test_rescan_idempotence, which pins the violation list. This
    pins the numbers computed from it.
    """
    url = "https://github.com/test/scoring-nochange.git"
    uid = make_user(9309, "scoring-nochange")

    first = scan_repo(repo, tmp_path / "c1", url, uid)
    second = scan_repo(
        repo, tmp_path / "c2", url, uid, previous=previous_run(repo, first)
    )

    before, after = _shape(first["run"]), _shape(second["run"])
    assert after["score"] == before["score"]
    assert after["band"] == before["band"]
    assert after["module_scores"] == before["module_scores"]
    assert after["violations"] == before["violations"]


@requires_postgres
def test_a_module_score_is_never_perfect_while_it_has_findings(repo, tmp_path, live_db):
    """The per-module half of the same defect.

    An untouched module read 100.0 -- a clean bill of health -- while its own
    violations were listed against it.
    """
    def edit(root: Path) -> None:
        p = root / "gamma" / "util.py"
        p.write_text(p.read_text(encoding="utf-8") + "import re\n", encoding="utf-8")

    incremental, _full = _incremental_vs_full(repo, tmp_path, edit, 9311)

    with_findings = {module for _layer, module, _msg in incremental["violations"]}
    perfect = [
        m for m in with_findings
        if incremental["module_scores"].get(m) == 100.0
    ]
    assert perfect == [], (
        f"these modules scored a perfect 100.0 while carrying findings: {perfect}"
    )
