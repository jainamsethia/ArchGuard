"""Layer 2 has to say whether it measured anything, and mean it.

The last of the four. Layers 1, 3 and 4 each report an unmeasured layer as
skipped so the composite reweights around it; Layer 2 had no skip channel at
all -- it returned a score and a violation list and nothing else -- so a loop
that ran over zero modules returned a measured 0.00 and the composite counted
it as a clean pass.

That is not a hypothetical. A contract whose module paths match no file in the
repository leaves every layer with nothing: Layer 1 has no import rules to
enforce, Layers 3 and 4 have no module in scope, and Layer 2 has no module to
compute fan-out for. Three of them said so. The fourth scored it, and the
repository came back 100.0/PASS having had nothing measured at all.

The composite already knows how to reweight around a skipped layer -- it takes
a list of names and averages over the rest -- so this is a fifth key in an
existing model rather than a new mechanism. What the model did not have is an
answer for "every layer was skipped", which it scored as 0.00 debt and
therefore as perfect health.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from archguard.analysis._layer_runners import _run_layer2
from archguard.analysis.scoring import LayerScores, compute_archdebt

CONTRACT: dict[str, Any] = {
    "modules": [
        {"name": "alpha", "path": "alpha/", "coupling_budget": 2},
        {"name": "beta", "path": "beta/", "coupling_budget": 2},
    ]
}

#: A contract whose module matches nothing in the tree below.
CONTRACT_MATCHING_NOTHING: dict[str, Any] = {
    "modules": [{"name": "ghost", "path": "ghost/", "coupling_budget": 2}]
}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Two modules, one of which imports enough to breach its budget."""
    for name in ("alpha", "beta"):
        (tmp_path / name).mkdir(parents=True)
        (tmp_path / name / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "alpha" / "core.py").write_text(
        "import beta.api\nimport third\nimport fourth\n", encoding="utf-8"
    )
    (tmp_path / "beta" / "api.py").write_text("import json\n", encoding="utf-8")
    return tmp_path


def _layer2(repo: Path, contract: dict, affected: dict) -> tuple[float, list, str]:
    return _run_layer2(
        repo_root=repo,
        contract=contract,
        affected=affected,
        commit_sha="abcdef1234",
    )


# --------------------------------------------------------- 1. empty scope


def test_no_module_in_scope_is_reported_as_unmeasured(repo: Path) -> None:
    """The regression. Nothing was measured, so 0.00 is not a coupling result."""
    _score, violations, skip_reason = _layer2(repo, CONTRACT, {})

    assert skip_reason, (
        "Layer 2 computed fan-out for no module and reported no reason, so the "
        "composite counts its 0.00 as a clean measured layer"
    )
    assert violations == []


def test_a_contract_matching_no_file_is_reported_as_unmeasured(repo: Path) -> None:
    """The same thing by the route a real repository reaches it.

    `affected` is built from the files that exist, so a module the contract
    declares but nothing matches simply never appears in it.
    """
    _score, _violations, skip_reason = _layer2(repo, CONTRACT_MATCHING_NOTHING, {})

    assert skip_reason


def test_a_module_the_contract_does_not_declare_is_not_a_measurement(
    repo: Path,
) -> None:
    """`_run_layer2` skips names it has no paths for. Skipping every one of them
    is still having measured nothing."""
    _score, _violations, skip_reason = _layer2(
        repo, CONTRACT, {"undeclared": [], "also-undeclared": []}
    )

    assert skip_reason


# ---------------------------------------------------- 2 & 3. measured scope


def test_a_clean_module_is_measured_not_skipped(repo: Path) -> None:
    """`beta` imports only stdlib, so its fan-out is under budget. That is a
    real 0.00 and it belongs in the score."""
    score, violations, skip_reason = _layer2(repo, CONTRACT, {"beta": []})

    assert skip_reason == "", (
        "a module that was measured and found clean was reported as unmeasured"
    )
    assert score == 0.0
    assert violations == []


def test_a_module_over_budget_still_scores_and_reports(repo: Path) -> None:
    """The layer's actual job, pinned so the reporting change cannot mute it."""
    score, violations, skip_reason = _layer2(repo, CONTRACT, {"alpha": []})

    assert skip_reason == ""
    assert score > 0.0
    assert [v.module for v in violations] == ["alpha"]
    assert violations[0].layer == 2


def test_one_measured_module_is_enough_for_the_layer_to_count(repo: Path) -> None:
    """Consistent with Layers 3 and 4: a signal from one module is not erased
    by another having nothing."""
    score, _violations, skip_reason = _layer2(
        repo, CONTRACT, {"alpha": [], "undeclared": []}
    )

    assert skip_reason == ""
    assert score > 0.0


# ------------------------------------------- 4. the composite does not inflate


def test_a_skipped_layer_2_is_reweighted_out_rather_than_averaged_in() -> None:
    """The consequence the skip state exists for.

    An unmeasured 0.00 averaged alongside a real finding drags the composite
    down towards healthy. Reweighting excludes it instead, which is what the
    composite already does for Layers 1, 3 and 4.
    """
    scores = LayerScores(0.0, 0.0, 0.6, 0.0)

    counted = compute_archdebt(scores, skipped=["Layer 1", "Layer 4"])
    reweighted = compute_archdebt(scores, skipped=["Layer 1", "Layer 2", "Layer 4"])

    assert counted.composite_score == pytest.approx(0.3)
    assert reweighted.composite_score == pytest.approx(0.6), (
        "an unmeasured Layer 2 halved the debt of the one layer that did measure"
    )


def test_a_run_with_no_measured_layer_is_not_healthy() -> None:
    """The all-skipped case, which the composite used to score as perfect.

    Averaging over an empty set gave 0.00 debt, and 0.00 debt is 100/100 and a
    passing band. A repository nothing could be measured on is not a healthy
    repository; it is an unknown one, and the product already has a way to say
    so -- `AnalysisResult.skipped` with a reason, which the dashboard renders
    as "not checked" rather than as a grade.
    """
    result = compute_archdebt(
        LayerScores(0.0, 0.0, 0.0, 0.0),
        skipped=["Layer 1", "Layer 2", "Layer 3", "Layer 4"],
    )

    assert result.health_score != 100.0, (
        "a run that measured nothing was scored as perfect health"
    )
    assert result.band.name != "HEALTHY"


def test_the_composite_is_unchanged_when_layers_were_measured() -> None:
    """The reweighting model itself is not being altered -- only which names
    reach it."""
    scores = LayerScores(0.1, 0.2, 0.3, 0.4)

    assert compute_archdebt(scores).composite_score == pytest.approx(0.25)
    assert compute_archdebt(scores, skipped=["Layer 1"]).composite_score == (
        pytest.approx(0.3)
    )
