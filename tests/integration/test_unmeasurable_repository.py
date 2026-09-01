"""A repository nothing could be measured on must not come back perfect.

Each of the four layers now reports when it had nothing to measure, so the
composite reweights around it. Reweighting around all four leaves nothing to
average, and an average over nothing is 0.00 debt -- which is 100/100 and a
passing band. So the last step of fixing the layers was making the run itself
say what it is: not healthy, unknown.

This is reachable without contriving anything. Contract generation names the
modules it could measure from history; a path in a committed `.archguard.yml`
can stop existing; a repository can be restructured between one scan and the
next. In every case the result was a confident 100.0/PASS for a scan that
looked at no code at all -- and `archguard/watch/service.py` compares scores
between runs to decide whether a watched repository regressed, so it would have
read the restructure as an improvement to perfect health.

Run against the real pipeline, because the property is a product of the whole
aggregation path -- layer runners, the metrics dict, `_finalize_result`, the
composite and the persisted payload -- and a unit test of any one of them would
miss a break in the handoff between two.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.db_fixtures import requires_postgres
from tests.integration._pipeline_scan import make_user, previous_run, scan_repo

pytestmark = pytest.mark.integration


#: Declares a module that matches no file in the tree below.
CONTRACT_MATCHING_NOTHING = """version: "3.0"
fail_threshold: 0.75
warn_threshold: 0.50
modules:
- name: ghost
  path: ghost/
  coupling_budget: 2
"""

#: Declares the module the code is actually in.
CONTRACT_MATCHING_THE_CODE = """version: "3.0"
fail_threshold: 0.75
warn_threshold: 0.50
modules:
- name: hub
  path: hub/
  coupling_budget: 2
"""

#: Declares import rules whose module path matches no file. Layer 1's own
#: instance of the same defect: its existing skip asks whether the contract
#: declares rules, this contract does, and so it reported a clean 0.00 from a
#: layer that opened nothing. With Layers 2, 3 and 4 skipped this was the only
#: layer left counting, and the repository scored 100/PASS on it alone.
CONTRACT_WITH_STALE_IMPORT_RULES = """version: "3.0"
fail_threshold: 0.75
warn_threshold: 0.50
modules:
- name: ghost
  path: ghost/
  coupling_budget: 2
  allowed_imports:
  - json
"""


def _tree(root: Path, contract: str) -> Path:
    (root / "hub").mkdir(parents=True)
    (root / "hub" / "__init__.py").write_text("", encoding="utf-8")
    # Over a coupling budget of two, so the matching contract has a finding and
    # "they agree" is not two empty results agreeing.
    (root / "hub" / "app.py").write_text(
        '"""The hub."""\n'
        "import wide\nimport other\nimport extra\nimport json\n\n\n"
        "def total():\n    return 1\n",
        encoding="utf-8",
    )
    for name in ("wide", "other", "extra"):
        (root / name).mkdir(parents=True)
        (root / name / "__init__.py").write_text("", encoding="utf-8")
        (root / name / "core.py").write_text(
            f'"""{name}."""\n\n\ndef run():\n    return 1\n', encoding="utf-8"
        )
    (root / ".archguard.yml").write_text(contract, encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    git("add", "-A")
    git("commit", "-q", "-m", "initial")
    return root


@pytest.fixture()
def unmeasurable(tmp_path: Path) -> Path:
    return _tree(tmp_path / "ghost", CONTRACT_MATCHING_NOTHING)


@pytest.fixture()
def measurable(tmp_path: Path) -> Path:
    return _tree(tmp_path / "real", CONTRACT_MATCHING_THE_CODE)


@pytest.fixture()
def stale_import_rules(tmp_path: Path) -> Path:
    return _tree(tmp_path / "stale", CONTRACT_WITH_STALE_IMPORT_RULES)


@pytest.fixture(autouse=True)
def _layers_enabled(monkeypatch):
    """ARCHGUARD_SKIP_ML unset, so Layers 3 and 4 reach their own runners.

    With it set they are skipped before the runner either way, and the test
    would pass without exercising the code it is about.
    """
    monkeypatch.delenv("ARCHGUARD_SKIP_ML", raising=False)


def _layers(run: dict) -> dict[int, dict]:
    return {lr["layer"]: lr for lr in (run.get("layer_results") or [])}


def _shape(run: dict) -> dict:
    return {
        "score": run.get("score"),
        "band": run.get("band"),
        "module_scores": run.get("module_scores") or {},
        "counted": sorted(
            lr["layer"] for lr in (run.get("layer_results") or []) if not lr["skipped"]
        ),
        "violations": sorted(
            (str(v.get("layer")), str(v.get("module")), str(v.get("message")))
            for v in (run.get("violations") or [])
        ),
    }


# ------------------------------------------- 5. cannot report PASS on nothing


@requires_postgres
def test_a_repository_nothing_could_be_measured_on_does_not_pass(
    unmeasurable, tmp_path, live_db
):
    """The defect, stated as the thing a user would have read.

    Before: 100.0, PASS, with all four layer rows saying "not checked".
    """
    uid = make_user(9401, "unmeasurable")
    run = scan_repo(
        unmeasurable, tmp_path / "c1", "https://github.com/test/unmeasurable.git", uid
    )["run"]

    assert _shape(run)["counted"] == [], (
        "a layer claimed to have measured a repository with nothing in scope"
    )
    assert run["score"] != 100.0, "a scan that measured nothing reported perfect health"
    assert run["band"] != "PASS", "a scan that measured nothing reported a pass"


@requires_postgres
def test_the_run_says_it_measured_nothing(unmeasurable, tmp_path, live_db):
    """A score with no explanation is worse than no score.

    The run carries the flag the dashboard uses to show a reason instead of a
    grade, and every layer explains itself.
    """
    uid = make_user(9403, "unmeasurable-why")
    run = scan_repo(
        unmeasurable, tmp_path / "c1", "https://github.com/test/unmeasurable-why.git", uid
    )["run"]

    assert run.get("skipped") is True, (
        "the run reported a score without recording that nothing produced it"
    )
    for layer, lr in _layers(run).items():
        assert lr["skipped"] is True, f"layer {layer} was counted"
        assert lr["skip_reason"], f"layer {layer} is marked skipped without a reason"


@requires_postgres
def test_layer_2_is_the_one_that_used_to_carry_it(unmeasurable, tmp_path, live_db):
    """Named directly, because Layer 2 was the last layer without a skip state
    and the only reason this repository used to score at all."""
    uid = make_user(9405, "unmeasurable-l2")
    run = scan_repo(
        unmeasurable, tmp_path / "c1", "https://github.com/test/unmeasurable-l2.git", uid
    )["run"]

    layer2 = _layers(run)[2]
    assert layer2["skipped"] is True
    assert "scope" in layer2["skip_reason"].lower()


# -------------------------------- Layer 1: rules declared, paths matching nothing


@requires_postgres
def test_stale_import_rules_do_not_carry_the_score(
    stale_import_rules, tmp_path, live_db
):
    """The last instance of the defect, end to end.

    This contract declares `allowed_imports`, so Layer 1's existing skip -- which
    asks whether any rules are declared -- does not fire. But its path matches
    no file, so every file resolved to no module and the layer examined nothing
    while reporting the clean 0.00 it reports for a repository it checked.

    With Layers 2, 3 and 4 all skipped, that left Layer 1 as the only counted
    layer, and 0.00 debt from one layer is 100/100 and a pass.
    """
    uid = make_user(9421, "stale-rules")
    run = scan_repo(
        stale_import_rules,
        tmp_path / "c1",
        "https://github.com/test/stale-rules.git",
        uid,
    )["run"]

    layer1 = _layers(run)[1]
    assert layer1["skipped"] is True, (
        "Layer 1 declared itself measured after examining no file against any "
        "rule, which is the whole defect"
    )
    assert "import rules" in layer1["skip_reason"], layer1["skip_reason"]
    assert _shape(run)["counted"] == [], (
        f"a layer claimed to have measured this repository: {_shape(run)['counted']}"
    )
    assert run["score"] != 100.0
    assert run["band"] != "PASS"


@requires_postgres
def test_the_two_layer_1_skip_reasons_stay_distinct(
    stale_import_rules, unmeasurable, tmp_path, live_db
):
    """Two different problems deserve two different answers.

    A contract with no import rules is ordinary and not worth acting on; a
    contract whose rules reach no file is broken. Reporting the first message
    for the second would send a reader looking for a missing `allowed_imports`
    that is right there.
    """
    stale = scan_repo(
        stale_import_rules,
        tmp_path / "c1",
        "https://github.com/test/stale-reason.git",
        make_user(9423, "stale-reason"),
    )["run"]
    no_rules = scan_repo(
        unmeasurable,
        tmp_path / "c2",
        "https://github.com/test/norules-reason.git",
        make_user(9424, "norules-reason"),
    )["run"]

    assert _layers(stale)[1]["skip_reason"] != _layers(no_rules)[1]["skip_reason"]
    assert "no import rules declared" in _layers(no_rules)[1]["skip_reason"]


@requires_postgres
def test_full_and_incremental_agree_on_stale_import_rules(
    stale_import_rules, tmp_path, live_db
):
    """Case F for this path specifically."""
    uid = make_user(9426, "stale-inc")
    url = "https://github.com/test/stale-inc.git"

    first = scan_repo(stale_import_rules, tmp_path / "c1", url, uid)

    path = stale_import_rules / "wide" / "core.py"
    path.write_text(
        path.read_text(encoding="utf-8") + "import collections\n", encoding="utf-8"
    )

    incremental = scan_repo(
        stale_import_rules,
        tmp_path / "c2",
        url,
        uid,
        previous=previous_run(stale_import_rules, first),
    )
    full = scan_repo(
        stale_import_rules,
        tmp_path / "c3",
        "https://github.com/test/stale-inc-full.git",
        make_user(9427, "stale-inc-full"),
    )

    assert _shape(incremental["run"]) == _shape(full["run"])


# ---------------------------------------- 2 & 3. a measurable repository still works


@requires_postgres
def test_a_measurable_repository_is_unaffected(measurable, tmp_path, live_db):
    """The other direction. Marking more things skipped would look like a fix
    until a real repository stopped being scored."""
    uid = make_user(9407, "measurable")
    run = scan_repo(
        measurable, tmp_path / "c1", "https://github.com/test/measurable.git", uid
    )["run"]

    shape = _shape(run)
    assert 2 in shape["counted"], (
        f"Layer 2 did not measure a repository its contract matches: "
        f"{_layers(run)[2]['skip_reason']!r}"
    )
    assert shape["violations"], "the fixture produced no findings to score"
    assert run["score"] is not None
    assert run["band"] != "PASS", "a repository over its coupling budget passed"


@requires_postgres
def test_a_clean_measurable_repository_still_scores_well(tmp_path, live_db):
    """A measured 0.00 is a real result and must keep its clean score.

    Distinguishing "measured, clean" from "measured nothing" is the whole
    point; collapsing them in the other direction would be the same defect
    upside down.
    """
    root = tmp_path / "clean"
    (root / "hub").mkdir(parents=True)
    (root / "hub" / "__init__.py").write_text("", encoding="utf-8")
    (root / "hub" / "app.py").write_text(
        '"""Only stdlib."""\nimport json\n\n\ndef go():\n    return json\n',
        encoding="utf-8",
    )
    (root / ".archguard.yml").write_text(CONTRACT_MATCHING_THE_CODE, encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    git("add", "-A")
    git("commit", "-q", "-m", "initial")

    uid = make_user(9409, "clean-repo")
    run = scan_repo(root, tmp_path / "c1", "https://github.com/test/clean-repo.git", uid)[
        "run"
    ]

    assert 2 in _shape(run)["counted"], "a clean module was reported as unmeasured"
    assert _layers(run)[2]["score"] == 0.0
    assert run["score"] == 100.0, "a measured, clean repository lost its clean score"
    assert run["band"] == "PASS"


# ------------------------------------- 6 & 8. incremental equivalence preserved


@requires_postgres
def test_full_and_incremental_agree_on_an_unmeasurable_repository(
    unmeasurable, tmp_path, live_db
):
    """C-1's guarantee, on the path this change touches."""
    uid = make_user(9411, "unmeasurable-inc")
    url = "https://github.com/test/unmeasurable-inc.git"

    first = scan_repo(unmeasurable, tmp_path / "c1", url, uid)

    path = unmeasurable / "wide" / "core.py"
    path.write_text(
        path.read_text(encoding="utf-8") + "import collections\n", encoding="utf-8"
    )

    incremental = scan_repo(
        unmeasurable, tmp_path / "c2", url, uid, previous=previous_run(unmeasurable, first)
    )
    full_uid = make_user(9412, "unmeasurable-inc-full")
    full = scan_repo(
        unmeasurable,
        tmp_path / "c3",
        "https://github.com/test/unmeasurable-inc-full.git",
        full_uid,
    )

    assert _shape(incremental["run"]) == _shape(full["run"])


@requires_postgres
def test_full_and_incremental_agree_on_a_measurable_one(measurable, tmp_path, live_db):
    uid = make_user(9414, "measurable-inc")
    url = "https://github.com/test/measurable-inc.git"

    first = scan_repo(measurable, tmp_path / "c1", url, uid)

    path = measurable / "hub" / "app.py"
    path.write_text(
        path.read_text(encoding="utf-8") + "\n\ndef more():\n    return 2\n",
        encoding="utf-8",
    )

    incremental = scan_repo(
        measurable, tmp_path / "c2", url, uid, previous=previous_run(measurable, first)
    )
    full_uid = make_user(9415, "measurable-inc-full")
    full = scan_repo(
        measurable,
        tmp_path / "c3",
        "https://github.com/test/measurable-inc-full.git",
        full_uid,
    )

    assert _shape(incremental["run"]) == _shape(full["run"])


@requires_postgres
def test_a_no_change_rescan_reports_the_same_thing(measurable, tmp_path, live_db):
    uid = make_user(9417, "measurable-nochange")
    url = "https://github.com/test/measurable-nochange.git"

    first = scan_repo(measurable, tmp_path / "c1", url, uid)
    second = scan_repo(
        measurable, tmp_path / "c2", url, uid, previous=previous_run(measurable, first)
    )

    assert _shape(second["run"]) == _shape(first["run"])
