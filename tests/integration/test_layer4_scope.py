"""Layer 4's reporting, through the real pipeline.

tests/unit/test_layer4_scope_reporting.py states the rule; this checks it
survives contact with a real repository, a real database and -- where the
worker extras are installed -- the real embedding model.

Two properties, and they pull in opposite directions, which is why both are
here. Layer 4 must not report a clean 0.00 for modules it never searched. And
it must still be *counted* on every scan where it did search, because its
established incremental policy is that a no-change scan may reuse it while any
module change recomputes it repository-wide. A fix for the first that quietly
stopped the layer counting would satisfy every assertion about skipping and
break the thing the layer is for.

Layer 4 is handed the repository-wide module map on both the incremental and
the full path, so unlike Layer 3 it cannot produce a score that depends on how
much was reused. That is asserted rather than assumed: it is the property that
makes this a reporting fix rather than a second scoring fix.
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


#: `hub` is declared and populated. Everything under `outside/` is real code in
#: no declared module, which is the ordinary shape for an auto-generated
#: contract.
CONTRACT = """version: "3.0"
fail_threshold: 0.75
warn_threshold: 0.50
modules:
- name: hub
  path: hub/
  coupling_budget: 2
"""

#: A contract whose only module matches no file in the repository. Not exotic:
#: contract generation names modules it measured from history, and a path can
#: stop existing between one scan and the next.
CONTRACT_MATCHING_NOTHING = """version: "3.0"
fail_threshold: 0.75
warn_threshold: 0.50
modules:
- name: ghost
  path: ghost/
  coupling_budget: 2
"""


def _git_init(root: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    git("add", "-A")
    git("commit", "-q", "-m", "initial")


def _write_tree(root: Path) -> None:
    (root / "hub").mkdir(parents=True)
    (root / "hub" / "__init__.py").write_text("", encoding="utf-8")
    # Two near-identical functions, so there is something for duplication to
    # find rather than an empty result agreeing with an empty result.
    (root / "hub" / "app.py").write_text(
        '"""The hub."""\n\n\n'
        "def total_alpha(values):\n"
        '    """Add up the values and return the total."""\n'
        "    running = 0\n"
        "    for value in values:\n"
        "        running = running + value\n"
        "    return running\n\n\n"
        "def total_beta(values):\n"
        '    """Add up the values and return the total."""\n'
        "    running = 0\n"
        "    for value in values:\n"
        "        running = running + value\n"
        "    return running\n",
        encoding="utf-8",
    )
    (root / "outside").mkdir(parents=True)
    (root / "outside" / "__init__.py").write_text("", encoding="utf-8")
    (root / "outside" / "core.py").write_text(
        '"""Outside every declared module."""\n\n\ndef run():\n    return 1\n',
        encoding="utf-8",
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    _write_tree(root)
    (root / ".archguard.yml").write_text(CONTRACT, encoding="utf-8")
    _git_init(root)
    return root


@pytest.fixture()
def repo_matching_nothing(tmp_path: Path) -> Path:
    root = tmp_path / "source-ghost"
    _write_tree(root)
    (root / ".archguard.yml").write_text(CONTRACT_MATCHING_NOTHING, encoding="utf-8")
    _git_init(root)
    return root


@pytest.fixture(autouse=True)
def _layers_enabled(monkeypatch):
    """ARCHGUARD_SKIP_ML unset, which is the point.

    With it set, Layer 4 is short-circuited before the runner and every
    assertion here would pass against a layer that never executed. The CI `ml`
    job leaves it unset and installs the worker extras, so these run against the
    real model there.
    """
    monkeypatch.delenv("ARCHGUARD_SKIP_ML", raising=False)


def _layer(run: dict, n: int) -> dict:
    for lr in run.get("layer_results") or []:
        if lr["layer"] == n:
            return lr
    raise AssertionError(f"no layer {n} in {run.get('layer_results')}")


def _shape(run: dict) -> dict:
    return {
        "score": run.get("score"),
        "band": run.get("band"),
        "module_scores": run.get("module_scores") or {},
        "layers_counted": sorted(
            f"L{lr['layer']}"
            for lr in (run.get("layer_results") or [])
            if not lr.get("skipped")
        ),
        "violations": sorted(
            (str(v.get("layer")), str(v.get("module")), str(v.get("message")))
            for v in (run.get("violations") or [])
        ),
    }


# ---------------------------------------------- 5. nothing belongs to a module


@requires_postgres
def test_a_contract_matching_no_file_does_not_report_clean_duplication(
    repo_matching_nothing, tmp_path, live_db
):
    """The reported defect, end to end.

    Nothing in this repository belongs to the declared module, so Layer 4 has
    nothing to search. It used to answer 0.00 with no reason, which the caller
    reads as "searched, no duplication" and averages into the health score.
    """
    uid = make_user(9901, "l4-ghost")
    scan = scan_repo(
        repo_matching_nothing,
        tmp_path / "c1",
        "https://github.com/test/l4-ghost.git",
        uid,
    )

    layer4 = _layer(scan["run"], 4)
    assert layer4["skipped"] is True, (
        "Layer 4 searched no modules and was still counted as a measured, "
        "clean layer"
    )
    assert layer4["skip_reason"], "the layer was marked skipped without saying why"
    assert layer4["score"] == 0.0

    counted = _shape(scan["run"])["layers_counted"]
    assert "L3" not in counted and "L4" not in counted, (
        f"a layer with nothing in scope was counted as measured: {counted}"
    )

    # All four layers now decline to score a repository they could not measure.
    # This assertion was the reminder that Layer 2 did not, and that it left the
    # repository reporting 100.0/PASS off the back of a layer that had looked at
    # nothing; it is kept, pointed at the answer, so a regression in any of the
    # four shows up here as a repository scoring well for having no content in
    # scope. The whole-run consequence is
    # tests/integration/test_unmeasurable_repository.py.
    assert counted == [], (
        f"a repository with nothing in scope still counted a layer: {counted}"
    )
    assert scan["run"].get("score") != 100.0, (
        "a repository where nothing could be measured was scored as perfect"
    )


# ------------------------------------------------------ 2. measurable scope


@requires_postgres
@requires_ml
def test_a_module_with_content_is_measured_and_counted(repo, tmp_path, live_db):
    """The other direction: a searched module must still count.

    Without this, marking more things skipped would look like a fix.

    `requires_ml` because "did not skip" is only a statement about this code
    where Layer 4 *can* run. With no extras installed it skips for that reason
    alone, and the assertion becomes a report on the installation.
    """
    uid = make_user(9903, "l4-real")
    scan = scan_repo(repo, tmp_path / "c1", "https://github.com/test/l4-real.git", uid)

    layer4 = _layer(scan["run"], 4)
    assert layer4["skipped"] is False, (
        f"Layer 4 did not measure a module that has functions: "
        f"{layer4['skip_reason']!r}"
    )


# ------------------------------------------------ 3 & 4. incremental scanning


@requires_postgres
def test_a_no_change_rescan_reports_layer_4_the_same_way(repo, tmp_path, live_db):
    """Nothing changed, so nothing about the result may change either -- and
    that includes whether Layer 4 counted."""
    uid = make_user(9905, "l4-nochange")
    url = "https://github.com/test/l4-nochange.git"

    first = scan_repo(repo, tmp_path / "c1", url, uid)
    second = scan_repo(
        repo, tmp_path / "c2", url, uid, previous=previous_run(repo, first)
    )

    assert _shape(second["run"]) == _shape(first["run"])
    assert _layer(second["run"], 4)["skipped"] == _layer(first["run"], 4)["skipped"]


@requires_postgres
def test_a_changed_module_still_recomputes_layer_4_repository_wide(
    repo, tmp_path, live_db
):
    """The established policy, pinned.

    Any module change recomputes Layer 4 across the whole repository, so an
    incremental scan of an edited tree must report the same thing a full scan
    of it does -- including which layers counted.
    """
    uid = make_user(9907, "l4-changed")
    url = "https://github.com/test/l4-changed.git"

    first = scan_repo(repo, tmp_path / "c1", url, uid)

    path = repo / "hub" / "app.py"
    path.write_text(
        path.read_text(encoding="utf-8")
        + '\n\ndef total_gamma(values):\n    """Add up the values and return the total."""\n'
        "    running = 0\n"
        "    for value in values:\n"
        "        running = running + value\n"
        "    return running\n",
        encoding="utf-8",
    )

    incremental = scan_repo(
        repo, tmp_path / "c2", url, uid, previous=previous_run(repo, first)
    )
    full_uid = make_user(9908, "l4-changed-full")
    full = scan_repo(
        repo, tmp_path / "c3", "https://github.com/test/l4-changed-full.git", full_uid
    )

    assert _layer(incremental["run"], 4)["skipped"] == _layer(full["run"], 4)["skipped"]
    assert _shape(incremental["run"]) == _shape(full["run"]), (
        "an incremental scan of the edited tree disagreed with a full scan"
    )


@requires_postgres
@requires_ml
def test_an_edit_outside_every_module_agrees_too(repo, tmp_path, live_db):
    """The Layer 3 case, checked for Layer 4.

    Editing a file in no declared module is what emptied Layer 3's scope. Layer
    4 is handed the repository-wide map regardless, so it must be unaffected --
    which is the reason this was a reporting fix rather than a second scoring
    one.
    """
    uid = make_user(9910, "l4-outside")
    url = "https://github.com/test/l4-outside.git"

    first = scan_repo(repo, tmp_path / "c1", url, uid)

    path = repo / "outside" / "core.py"
    path.write_text(
        path.read_text(encoding="utf-8") + "import collections\nimport itertools\n",
        encoding="utf-8",
    )

    incremental = scan_repo(
        repo, tmp_path / "c2", url, uid, previous=previous_run(repo, first)
    )
    full_uid = make_user(9911, "l4-outside-full")
    full = scan_repo(
        repo, tmp_path / "c3", "https://github.com/test/l4-outside-full.git", full_uid
    )

    assert _layer(incremental["run"], 4)["skipped"] is False, (
        "an edit outside every module stopped Layer 4 measuring the repository"
    )
    assert _shape(incremental["run"]) == _shape(full["run"])
