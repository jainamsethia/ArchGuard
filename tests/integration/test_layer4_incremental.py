"""Layer 4 across the incremental boundary, against real embeddings.

Layers 1-3 are *attributable*: a finding belongs to the file or module that
produced it, so skipping an unchanged module and carrying its old findings
forward is sound. Layer 4 is *relational*. A duplication finding names two
files -- the message reads ``a.py <-> b.py`` -- and is filed against one of
their modules. Its truth depends on both.

That difference is the bug these tests pin. Edit the clone out of module_a and
module_b is still "clean" by every hash the planner has, so its copy of the
finding used to be carried forward: the report told a user about a duplication
of code they had just deleted. Worse, Layer 4 was handed only the changed
slice, so the recomputed half could not see the unchanged file it needed to
compare against.

Real ML, not mocks. A faked embedding index would happily prove whatever the
mock was told to return, and what is being tested here is precisely whether the
real corpus contains the real vectors of the real unchanged files.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.db_fixtures import requires_postgres
from tests.integration._pipeline_scan import (
    identity,
    make_user,
    previous_run,
    requires_ml,
    scan_repo,
    violations_of,
)

pytestmark = pytest.mark.integration

#: The fixture ships a contract declaring one ``misc`` module covering ``./``,
#: which puts both files in the same module -- and Layer 4 reports cross-module
#: clones only. Splitting them is what makes the planted duplication visible.
CROSS_MODULE_CONTRACT = (
    "version: '3.0'\n"
    "modules:\n"
    "- name: module_a\n  path: module_a/\n"
    "- name: module_b\n  path: module_b/\n"
)

#: module_a/a.py, rewritten into something with no relationship to module_b.
#: The file survives, so module_a is dirty and module_b is not -- exactly the
#: asymmetry that used to strand the finding on the clean side.
#:
#: ``some_other_func_a`` goes too, deliberately. It and module_b's
#: ``some_other_func_b`` are both a bare ``pass``, so they embed almost
#: identically and Layer 4 reports them as a clone in their own right -- a
#: genuine finding, but one that would leave a Layer 4 violation standing
#: against module_b for an honest reason and make the assertion below unable to
#: tell a real finding from a stale one. After this rewrite the two modules
#: share nothing, so any remaining Layer 4 finding is stale by construction.
REWRITTEN_A = (
    'def greet_a(name: str) -> str:\n'
    '    """Nothing to do with module B any more."""\n'
    '    return "hello " + name\n'
)


@pytest.fixture()
def clone_repo(tmp_path: Path) -> Path:
    """The planted cross-module clone, under a two-module contract.

    This is the *source* the scans clone from, not a working tree. Tests mutate
    it between scans the way a user would push a commit; each scan then copies
    it fresh -- see ``scan_repo``.
    """
    src = Path(__file__).resolve().parents[1] / "fixtures" / "planted_duplication"
    repo = tmp_path / "source"
    shutil.copytree(src, repo, ignore=shutil.ignore_patterns(".git", ".archguard-cache"))
    # Written once, before the first scan. Rewriting it between scans would
    # change the contract fingerprint and force a full analysis, which is the
    # one thing that would make every assertion below pass for the wrong reason.
    (repo / ".archguard.yml").write_text(CROSS_MODULE_CONTRACT, encoding="utf-8")
    return repo


def _layer4(run: dict) -> list[dict]:
    """Layer 4 findings only."""
    return violations_of(run, "4")


# --------------------------------------------------------------- the reported bug


@requires_ml
@requires_postgres
def test_a_stale_clone_disappears_when_its_counterpart_changes(clone_repo, tmp_path, live_db):
    """Rewrite the clone out of one module; the other module's copy of the
    finding must not survive."""
    user_id = make_user(7301, "l4-stale")
    url = "https://github.com/test/l4-stale.git"

    first = scan_repo(clone_repo, tmp_path / "clone1", url, user_id)
    assert _layer4(first["run"]), (
        "the fixture produced no cross-module duplication, so every assertion "
        "below would pass vacuously; Layer 4 or the contract has regressed"
    )

    (clone_repo / "module_a" / "a.py").write_text(REWRITTEN_A, encoding="utf-8")
    second = scan_repo(
        clone_repo, tmp_path / "clone2", url, user_id,
        previous=previous_run(clone_repo, first),
    )
    after = _layer4(second["run"])

    # Matched on the file path, not the function name. A Layer 4 message is
    # built from `m.source_function.split("::")[0]` (_layer_runners.py), so it
    # reads "module_a/a.py <-> module_b/b.py" and never contains a function
    # name -- a filter on "process_data" would match nothing and pass whatever
    # the code did.
    stale = [v for v in after if "module_a/a.py" in str(v.get("message") or "")]
    assert stale == [], (
        "a duplication finding for code that was rewritten survived the rescan: "
        f"{[v.get('message') for v in stale]}"
    )
    assert [v for v in after if v.get("module") == "module_b"] == [], (
        "the untouched module kept a carried-forward duplication finding"
    )


@requires_ml
@requires_postgres
def test_the_unchanged_module_is_not_left_holding_a_false_finding(clone_repo, tmp_path, live_db):
    """The same defect stated from the other side, and the reason it is subtle.

    Deleting the file outright dirties *nothing*: a path that is gone simply
    does not appear in the file list, so no hash differs and the planner
    believes the repository is untouched. Any rule of the form "re-run Layer 4
    when something changed" is blind to exactly this case, which is why the
    rule is that a Layer 4 finding is never carried at all.
    """
    user_id = make_user(7304, "l4-deleted")
    url = "https://github.com/test/l4-deleted.git"

    first = scan_repo(clone_repo, tmp_path / "clone1", url, user_id)
    assert _layer4(first["run"]), "no duplication to begin with"

    (clone_repo / "module_a" / "a.py").unlink()
    second = scan_repo(
        clone_repo, tmp_path / "clone2", url, user_id,
        previous=previous_run(clone_repo, first),
    )

    assert _layer4(second["run"]) == [], (
        "the clone's counterpart was deleted, but a duplication finding remains"
    )


# --------------------------------------------------------- reuse and idempotence


@requires_ml
@requires_postgres
def test_an_unchanged_rescan_still_reports_the_clone(clone_repo, tmp_path, live_db):
    """Dropping Layer 4 from the carry-forward set must not lose the finding.

    Nothing changed, the clone is still there, and the rescan must still say
    so -- re-measured rather than carried, so the number a user sees is one
    this scan actually established.
    """
    user_id = make_user(7302, "l4-unchanged")
    url = "https://github.com/test/l4-unchanged.git"

    first = scan_repo(clone_repo, tmp_path / "clone1", url, user_id)
    before = _layer4(first["run"])
    assert before, "no duplication to begin with"

    second = scan_repo(
        clone_repo, tmp_path / "clone2", url, user_id,
        previous=previous_run(clone_repo, first),
    )
    after = _layer4(second["run"])

    assert after, "an unchanged rescan lost the duplication finding entirely"
    assert identity(after) == identity(before), (
        "an unchanged rescan reported different duplication than the scan before it"
    )


@requires_ml
@requires_postgres
def test_the_clone_is_still_found_when_only_one_side_changed(clone_repo, tmp_path, live_db):
    """The other half of the fix, and the only test that exercises it.

    Touch module_a and leave the clone intact. module_a is dirty, module_b is
    not, and the duplication is still genuinely there -- so the rescan must
    still report it, against both modules.

    This is what the widened file set buys. Layer 4 matches against the
    embedding corpus, and the corpus is written per scan into the clone's own
    database. Scoped to the changed slice, only module_a's vectors would exist,
    module_b's half of the clone would have nothing to match against, and the
    scan would report the repository as clone-free while the clone sat there
    untouched. Reverting `duplication_files` or the corpus loop in
    `_run_layer4` fails on the assertions below and nowhere else.

    Also pins the no-double-count property: Layer 4 recomputes over everything,
    so carrying it forward as well would report the same clone twice.
    """
    user_id = make_user(7303, "l4-onesided")
    url = "https://github.com/test/l4-onesided.git"

    first = scan_repo(clone_repo, tmp_path / "clone1", url, user_id)
    assert _layer4(first["run"]), "no duplication to begin with"

    # A comment, so the file's hash changes and nothing else does. The clone
    # itself survives verbatim.
    path = clone_repo / "module_a" / "a.py"
    path.write_text(path.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")

    second = scan_repo(
        clone_repo, tmp_path / "clone2", url, user_id,
        previous=previous_run(clone_repo, first),
    )
    after = _layer4(second["run"])

    assert after, (
        "the clone is still in the repository, but the incremental rescan "
        "reported no duplication at all -- Layer 4 was scoped to the changed "
        "slice and could not see module_b"
    )
    assert {v.get("module") for v in after} == {"module_a", "module_b"}, (
        "the clone spans both modules and must be reported against both; got "
        f"{sorted(str(v.get('module')) for v in after)}"
    )

    messages = [str(v.get("message") or "") for v in after]
    assert len(messages) == len(set(messages)), (
        f"the same duplication was reported more than once: {messages}"
    )


# ------------------------------------------- layers 2 and 3 keep their scoping


@requires_ml
@requires_postgres
def test_the_incremental_scan_still_skips_the_unchanged_module(clone_repo, live_db):
    """The other half of the requirement: widening Layer 4 must not quietly
    turn every incremental scan into a full one.

    Asserted on the plan rather than on timings, which are too noisy to gate a
    build on.
    """
    from archguard.cache.incremental import PreviousRun, hash_files, plan_analysis
    from archguard.dashboard.pipeline_adapter import (
        _archguard_version,
        _safe_load_contract,
    )

    files = sorted(clone_repo.rglob("*.py"))
    contract = _safe_load_contract(clone_repo)
    known = hash_files(files, clone_repo)

    (clone_repo / "module_a" / "a.py").write_text(REWRITTEN_A, encoding="utf-8")

    plan = plan_analysis(
        files=sorted(clone_repo.rglob("*.py")),
        root=clone_repo,
        contract=contract,
        version=_archguard_version(),
        previous=PreviousRun(
            contract=contract,
            archguard_version=_archguard_version(),
            file_hashes=known,
            violations=[
                {"module": "module_b", "layer": 2, "message": "fan_out=9"},
                {"module": "module_b", "layer": 3, "message": "semantic drift 0.41"},
                {"module": "module_b", "layer": 4, "message": "a.py <-> b.py"},
            ],
        ),
    )

    assert plan.full is False, "an edit to one module forced a full analysis"
    assert plan.dirty_modules == {"module_a"}, "the untouched module was re-analysed"
    assert [p.name for p in plan.changed] == ["a.py"]

    # Layer 3 is the only one carried. Layers 2 and 4 measure the whole
    # repository on every scan, so carrying their findings would list each of
    # them twice -- and their scores would still be right either way, which is
    # what makes carrying them pure downside.
    assert identity(plan.carried_violations) == [
        ("3", "module_b", "semantic drift 0.41")
    ]

    # And the repository-wide layers are handed every file, including the one
    # that did not change -- without which a recomputed clone has nothing to
    # compare against and an untouched module contributes nothing to the score.
    assert sorted(plan.repo_files) == sorted(clone_repo.rglob("*.py"))
