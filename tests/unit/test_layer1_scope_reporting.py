"""Layer 1 has to say whether it examined anything, and mean it.

The last instance of a defect closed four times over: a layer that measured
nothing reporting a clean 0.00, which the composite averages in as a pass.

Layer 1 already had a skip state, but it fired on the wrong question. It asked
whether the *contract* declares any import rules, which catches the common case
-- an auto-generated contract declares none -- and misses the one that matters:
a contract that declares rules whose module paths no longer match any file.
Then every file resolves to no module, `_analyze_file_imports` returns early
for all of them, and the score is `0 / max(0, 1)` -- a measured, clean zero
from a layer that opened no file.

What counts as measured here is deliberately not what counts for Layer 2, and
this is the reason the fix could not be copied across. Layer 2 measures a
module, so "no module in scope" is its whole answer. Layer 1 measures a *file
against its module's rules*, and a file in a rule-bearing module that imports
only stdlib has been examined and found compliant -- vacuously, but really. The
question is whether any file was ever put in front of a rule, not whether any
import happened to be counted. Using the import count would report a genuinely
clean repository as unmeasured, which is the same defect inverted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from archguard.analysis._layer_runners import _run_layer1
from archguard.analysis.scoring import LayerScores, compute_archdebt

#: Declares rules, and its path matches the tree below.
MATCHING: dict[str, Any] = {
    "modules": [
        {"name": "app", "path": "app/", "allowed_imports": ["shared"]},
        {"name": "shared", "path": "shared/"},
    ]
}

#: Declares rules, and its path matches nothing. The reported defect.
STALE: dict[str, Any] = {
    "modules": [{"name": "ghost", "path": "ghost/", "allowed_imports": ["shared"]}]
}

#: Declares no rules at all -- the case the existing skip already caught.
NO_RULES: dict[str, Any] = {
    "modules": [{"name": "app", "path": "app/"}, {"name": "shared", "path": "shared/"}]
}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    for name in ("app", "shared", "sneaky"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "main.py").write_text(
        "import shared.util\nimport json\n", encoding="utf-8"
    )
    (tmp_path / "app" / "quiet.py").write_text("import json\n", encoding="utf-8")
    (tmp_path / "shared" / "util.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / "sneaky" / "bad.py").write_text("import sneaky\n", encoding="utf-8")
    return tmp_path


def _files(repo: Path, *names: str) -> list[Path]:
    return [repo / n for n in names]


def _layer1(repo: Path, contract: dict, files: list[Path]) -> tuple[float, list, str]:
    return _run_layer1(
        repo_root=repo,
        contract=contract,
        py_files=files,
        affected={},
        commit_sha="abcdef1234",
    )


# ------------------------------------------------ Case A: stale contract paths


def test_a_contract_whose_paths_match_nothing_is_unmeasured(repo: Path) -> None:
    """The defect. Rules are declared, so the existing skip does not fire, and
    no file resolves to a module, so nothing is examined."""
    score, violations, skip_reason = _layer1(
        repo, STALE, _files(repo, "app/main.py", "app/quiet.py", "shared/util.py")
    )

    assert skip_reason, (
        "Layer 1 examined no file against any rule and reported no reason, so "
        "the composite counts its 0.00 as a clean measured layer"
    )
    assert score == 0.0
    assert violations == []


def test_the_stale_path_reason_is_not_the_no_rules_reason(repo: Path) -> None:
    """Two different problems that need two different answers.

    "This contract declares no import rules" is the ordinary state of an
    auto-generated contract and is not worth acting on. "This contract declares
    rules and none of its paths match a file" is a broken contract, and a
    reader sent looking for a missing `allowed_imports` would never find it.
    """
    _score, _violations, stale_reason = _layer1(
        repo, STALE, _files(repo, "app/main.py")
    )

    assert "no import rules declared" not in stale_reason
    # Points at the relationship that is actually broken -- files and the
    # modules whose rules should cover them -- rather than at the rules.
    assert "file" in stale_reason and "import rules" in stale_reason


def test_files_outside_every_module_do_not_count_as_examined(repo: Path) -> None:
    """A file that belongs to no module is skipped by the analyser, so a scan
    consisting only of those has examined nothing."""
    _score, _violations, skip_reason = _layer1(
        repo, MATCHING, _files(repo, "sneaky/bad.py")
    )

    assert skip_reason


# ------------------------------------------ Case B: genuinely measured and clean


def test_a_matching_contract_with_no_violations_is_measured(repo: Path) -> None:
    """`app/main.py` imports `shared`, which its rules allow. Examined, clean,
    and it belongs in the score."""
    score, violations, skip_reason = _layer1(
        repo, MATCHING, _files(repo, "app/main.py")
    )

    assert skip_reason == "", "a file examined against its rules was called unmeasured"
    assert score == 0.0
    assert violations == []


def test_a_file_with_only_stdlib_imports_is_still_measured(repo: Path) -> None:
    """The case that rules out counting imports instead of files.

    `app/quiet.py` is in a module with rules and imports only stdlib, so no
    import is ever counted against a rule. It was still opened, resolved to its
    module and found compliant. Calling that unmeasured would report a
    genuinely clean repository as unchecked -- the same defect inverted, and
    the reason Layer 2's "was any module in scope" test does not transfer here.
    """
    score, _violations, skip_reason = _layer1(
        repo, MATCHING, _files(repo, "app/quiet.py")
    )

    assert skip_reason == ""
    assert score == 0.0


def test_a_module_without_rules_does_not_make_the_layer_measured(repo: Path) -> None:
    """`shared` declares no rules of its own, so a scan of only its files puts
    nothing in front of a rule."""
    _score, _violations, skip_reason = _layer1(
        repo, MATCHING, _files(repo, "shared/util.py")
    )

    assert skip_reason


def test_a_contract_declaring_no_rules_at_all_is_unmeasured(repo: Path) -> None:
    """Already caught by the contract-level check, and true here too -- the two
    must not disagree."""
    _score, _violations, skip_reason = _layer1(
        repo, NO_RULES, _files(repo, "app/main.py")
    )

    assert skip_reason


# --------------------------------------------- Case C: measured with findings


def test_a_real_violation_is_still_found_and_scored(repo: Path) -> None:
    """The layer's actual job, pinned so the reporting change cannot mute it."""
    contract = {
        "modules": [
            {"name": "app", "path": "app/", "disallowed_imports": ["shared"]},
            {"name": "shared", "path": "shared/"},
        ]
    }

    score, violations, skip_reason = _layer1(
        repo, contract, _files(repo, "app/main.py")
    )

    assert skip_reason == ""
    assert score > 0.0
    assert len(violations) == 1
    assert violations[0].layer == 1
    assert violations[0].module == "app"


def test_one_examined_file_is_enough_for_the_layer_to_count(repo: Path) -> None:
    """Consistent with Layers 2, 3 and 4: a signal from one unit is not erased
    by another having nothing."""
    _score, _violations, skip_reason = _layer1(
        repo, MATCHING, _files(repo, "app/main.py", "sneaky/bad.py")
    )

    assert skip_reason == ""


# ----------------------------------------------- Case D: mixed layer state


def test_a_skipped_layer_1_is_reweighted_out_rather_than_averaged_in() -> None:
    """The consequence the skip state exists for."""
    scores = LayerScores(0.0, 0.6, 0.0, 0.0)

    counted = compute_archdebt(scores, skipped=["Layer 3", "Layer 4"])
    reweighted = compute_archdebt(scores, skipped=["Layer 1", "Layer 3", "Layer 4"])

    assert counted.composite_score == pytest.approx(0.3)
    assert reweighted.composite_score == pytest.approx(0.6), (
        "an unmeasured Layer 1 halved the debt of the layer that did measure"
    )


def test_a_skipped_layer_1_cannot_carry_the_score_on_its_own() -> None:
    """The exact shape of the reported defect: Layers 2, 3 and 4 skipped, and
    Layer 1's unmeasured 0.00 left as the only thing averaged."""
    all_skipped = compute_archdebt(
        LayerScores(0.0, 0.0, 0.0, 0.0),
        skipped=["Layer 1", "Layer 2", "Layer 3", "Layer 4"],
    )
    only_layer_1 = compute_archdebt(
        LayerScores(0.0, 0.0, 0.0, 0.0),
        skipped=["Layer 2", "Layer 3", "Layer 4"],
    )

    assert all_skipped.health_score != 100.0
    assert all_skipped.band.name != "HEALTHY"
    # Left counted, Layer 1 alone still produces the perfect score the whole
    # series of fixes exists to prevent -- which is why it must be marked.
    assert only_layer_1.health_score == 100.0


def test_a_run_with_no_python_files_is_not_perfect() -> None:
    """The same defect one level up from the layers.

    The orchestrator returns early when the scan contains no Python file, and
    computed the composite without naming any layer as skipped -- so four
    unmeasured zeros were averaged as four measured ones, and a run with
    nothing in it came back 100/100 and passing while reporting itself skipped.
    """
    from archguard.analysis._orchestrator_run import _run_orchestrator

    result = _run_orchestrator(
        orchestrator=None, changed_files=[], commit_sha="abcdef1234", quiet=True
    )

    assert result.skipped is True
    assert result.archdebt.health_score != 100.0, (
        "a run containing no Python files reported perfect health"
    )
    assert result.archdebt.band.name != "HEALTHY"
