"""Which layers a scan counted must not depend on how much of it was reused.

`test_incremental_scoring.py` established that an incremental scan must report
the same numbers as a full one, and covers it with a committed contract under
which every file belongs to a module. That is the well-behaved case, and it
hid this one.

When a file belongs to no declared module, an incremental scan hands Layer 3 an
empty module map. Layer 3 then measured nothing and said nothing about it,
which its caller reads as "ran, found no drift" -- so a layer that never
executed contributed a clean 0.00 to the health score. A full scan of the same
tree hands it every module, gets "no prior baseline", and leaves the layer out
of the composite entirely. The composite is an average over the layers that
ran, so the same repository scored 93.3 one way and 90.0 the other.

That shape is the normal one for the population this matters to. A repository
with no committed `.archguard.yml` gets a contract generated per scan, and
generation names only the modules it could measure -- everything else in the
tree is outside every module. So an edit to an ordinary file produces exactly
the empty map above.

These tests therefore compare the *set of layers that counted* as well as the
numbers. The score is the symptom; which layers were measured is the thing that
differed, and asserting on it means a future regression says what broke rather
than only that a number moved.

Layer 3 is left running: `ARCHGUARD_SKIP_ML` unset is the point, since setting
it makes both paths skip the layer for the same reason and the divergence
disappears. When the worker extras are installed -- as they are in the CI `ml`
job -- this runs the real embedding model.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.db_fixtures import requires_postgres
from tests.integration._pipeline_scan import (
    make_user,
    previous_run,
    requires_ml,
    scan_repo,
)

pytestmark = pytest.mark.integration


#: One module declared, and a `wide/` package deliberately outside it. Editing
#: anything under `wide/` is what produces the empty module map. Committed
#: rather than generated, so the scenario does not depend on what contract
#: generation happens to decide today -- the generated-contract path is covered
#: end to end in test_generated_contract_rescan.py.
CONTRACT = """version: "3.0"
fail_threshold: 0.75
warn_threshold: 0.50
modules:
- name: hub
  path: hub/
  coupling_budget: 2
"""


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A repository whose contract covers some of it, which is the usual case.

    `hub` breaches its coupling budget, so there is a finding to compare rather
    than two empty lists agreeing with each other.
    """
    root = tmp_path / "source"
    (root / "hub").mkdir(parents=True)
    (root / "hub" / "__init__.py").write_text("", encoding="utf-8")

    # Five packages, none of them declared in the contract. Five so that `hub`
    # actually breaches its budget of two: fan-out counts unique non-stdlib
    # import roots, so a fixture with two would sit exactly on the limit and
    # produce nothing to compare.
    outside = ("wide", "other", "extra", "spare", "aux")
    for name in outside:
        (root / name).mkdir(parents=True)
        (root / name / "__init__.py").write_text("", encoding="utf-8")
        (root / name / "core.py").write_text(
            f'"""The {name} package."""\n\n\ndef run() -> int:\n    return 1\n',
            encoding="utf-8",
        )

    (root / "hub" / "app.py").write_text(
        "".join(f"import {name}.core\n" for name in outside)
        + "import json\n\n\ndef total() -> int:\n    return "
        + " + ".join(f"{name}.core.run()" for name in outside)
        + "\n",
        encoding="utf-8",
    )
    (root / ".archguard.yml").write_text(CONTRACT, encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    git("add", "-A")
    git("commit", "-q", "-m", "initial")
    return root


def _layers_that_counted(run: dict) -> set[str]:
    """The layers whose score went into the composite.

    This is the quantity that actually differed. `compute_archdebt` averages
    over the layers that ran and reweights around the ones that did not, so two
    scans disagreeing here disagree about the score by construction.
    """
    return {
        f"L{lr['layer']}"
        for lr in (run.get("layer_results") or [])
        if not lr.get("skipped")
    }


def _shape(run: dict) -> dict:
    """Everything a reader could compare between two scans of one state."""
    return {
        "score": run.get("score"),
        "band": run.get("band"),
        "module_scores": run.get("module_scores") or {},
        "layers_counted": sorted(_layers_that_counted(run)),
        "violations": sorted(
            (
                str(v.get("layer")),
                str(v.get("module")),
                str(v.get("severity")),
                str(v.get("kind")),
                str(v.get("message")),
            )
            for v in (run.get("violations") or [])
        ),
    }


def _both_ways(repo: Path, tmp_path: Path, edit, seed: int) -> tuple[dict, dict]:
    """Scan, edit, then measure the edited state incrementally and in full.

    The comparison is against a full scan of the *edited* tree. Comparing
    against the first scan would assert that an edit changes nothing, which is
    a different -- and wrong -- property.
    """
    url = f"https://github.com/test/layer-scope-{seed}.git"
    uid = make_user(seed, f"scope-{seed}")

    first = scan_repo(repo, tmp_path / "c1", url, uid)
    edit(repo)

    incremental = scan_repo(
        repo, tmp_path / "c2", url, uid, previous=previous_run(repo, first)
    )
    # A fresh account and URL, so this one has no previous run to reuse.
    full_uid = make_user(seed + 1, f"scope-{seed}-full")
    full = scan_repo(
        repo,
        tmp_path / "c3",
        f"https://github.com/test/layer-scope-{seed}-full.git",
        full_uid,
    )
    return _shape(incremental["run"]), _shape(full["run"])


def _edit_outside_every_module(root: Path) -> None:
    """The reproducing edit: a real change to a file in no declared module."""
    path = root / "wide" / "core.py"
    path.write_text(
        path.read_text(encoding="utf-8") + "import collections\nimport itertools\n",
        encoding="utf-8",
    )


def _edit_inside_a_module(root: Path) -> None:
    path = root / "hub" / "app.py"
    path.write_text(
        path.read_text(encoding="utf-8") + "\n\ndef extra() -> int:\n    return 2\n",
        encoding="utf-8",
    )


# --------------------------------------------------------- the reported defect


@requires_postgres
def test_an_edit_outside_every_module_scores_the_same_either_way(
    repo, tmp_path, live_db, monkeypatch
):
    """The exact reproduction, stated as the property it violates.

    Before the fix: incremental 93.3, full 90.0, with identical findings and
    identical per-module scores underneath both. The only difference was that
    the incremental scan counted Layer 3 as a measured clean pass when it had
    measured nothing at all.
    """
    monkeypatch.delenv("ARCHGUARD_SKIP_ML", raising=False)

    incremental, full = _both_ways(repo, tmp_path, _edit_outside_every_module, 9801)

    assert incremental["violations"], (
        "the fixture produced no findings, so agreement proves nothing"
    )
    assert incremental["layers_counted"] == full["layers_counted"], (
        "the two scans disagree about which layers ran, so their scores are "
        "averages over different sets of layers"
    )
    assert incremental == full


@requires_postgres
def test_a_layer_that_measured_nothing_is_not_counted_as_a_pass(
    repo, tmp_path, live_db, monkeypatch
):
    """The mechanism, on its own.

    Stated separately from the score so that a future change which happens to
    make the numbers agree for some other reason cannot pass this.
    """
    monkeypatch.delenv("ARCHGUARD_SKIP_ML", raising=False)

    incremental, _full = _both_ways(repo, tmp_path, _edit_outside_every_module, 9803)

    assert "L3" not in incremental["layers_counted"], (
        "Layer 3 had no module in scope on this scan and was still counted as "
        "a measured layer, so its unmeasured 0.00 is in the health score"
    )


# ------------------------------------------------------------ the C-1 cases


@requires_postgres
@pytest.mark.parametrize("skip_ml", ["", "1"], ids=["ml-enabled", "ml-disabled"])
@pytest.mark.parametrize(
    ("case", "edit"),
    [
        ("changed-module", _edit_inside_a_module),
        ("unchanged-modules-only", _edit_outside_every_module),
        ("no-change", lambda _root: None),
    ],
)
def test_incremental_agrees_with_full(
    repo, tmp_path, live_db, monkeypatch, skip_ml, case, edit
):
    """C-1's guarantee across the cases that reach this code path.

    Both settings of `ARCHGUARD_SKIP_ML`, because they take different routes to
    Layer 3: unset runs the layer (the real model when the extras are present),
    set short-circuits it before the runner. Only the first could diverge, and
    only the first did -- the second is here so that a fix which works by
    disabling the layer is visibly not a fix.
    """
    if skip_ml:
        monkeypatch.setenv("ARCHGUARD_SKIP_ML", skip_ml)
    else:
        monkeypatch.delenv("ARCHGUARD_SKIP_ML", raising=False)

    seed = 9810 + hash((case, skip_ml)) % 100 * 10
    incremental, full = _both_ways(repo, tmp_path, edit, seed)

    assert incremental == full, (
        f"[{case}, ml={'off' if skip_ml else 'on'}] an incremental scan "
        f"disagreed with a full scan of the same repository state"
    )


@requires_postgres
@requires_ml
def test_layer_4_is_still_recomputed_when_a_module_changed(
    repo, tmp_path, live_db, monkeypatch
):
    """The Layer 4 policy this must not quietly undo.

    Duplication is measured repository-wide whenever anything changed, so it is
    counted on an incremental scan exactly as it is on a full one. A fix that
    made layers agree by skipping more of them would show up here.
    """
    monkeypatch.delenv("ARCHGUARD_SKIP_ML", raising=False)

    incremental, full = _both_ways(repo, tmp_path, _edit_outside_every_module, 9805)

    assert "L4" in incremental["layers_counted"], (
        "Layer 4 stopped being measured on an incremental scan"
    )
    assert "L4" in full["layers_counted"]
    assert "L2" in incremental["layers_counted"], (
        "Layer 2 stopped being measured on an incremental scan"
    )
