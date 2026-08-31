"""Layer 4 has to say whether it measured anything, and mean it.

The same defect Layer 3 had, in the sibling function. `_run_layer4` reports
"did I run?" out of band, as an empty skip reason, and returned an empty one
when there was nothing to measure -- so the caller scored an unmeasured 0.00 as
a clean pass and averaged it into the health score.

Layer 4 could not produce the *incremental vs full* discrepancy Layer 3 did,
because it is handed the repository-wide module map on both paths and therefore
sees the same modules either way. That is why it is a separate commit and not
the same bug. What it could do, and did, is tell someone their repository has
no duplication when nothing was ever searched.

Three states have to be distinguishable through a single string, because that
is the whole channel the caller reads:

    measured, clean     -> no reason, and a real 0.00 enters the composite
    nothing measurable  -> a reason, and the layer is reweighted out
    unavailable         -> a reason, from the analyzer

These tests state that contract directly. The end-to-end behaviour against a
real repository, a real database and the real model is
tests/integration/test_layer4_scope.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from archguard.analysis._layer_runners import _run_layer4

CONTRACT: dict[str, Any] = {
    "modules": [
        {"name": "alpha", "path": "alpha/", "duplication_threshold": 0.5},
        {"name": "beta", "path": "beta/", "duplication_threshold": 0.5},
    ]
}


class _Result:
    """One module's answer, in the shape `analyze_module` returns."""

    def __init__(
        self, score: float = 0.0, skipped: bool = False, reason: str = ""
    ) -> None:
        self.aggregate_score = score
        self.skipped = skipped
        self.skip_reason = reason
        self.matches: list[Any] = []


class _Analyzer:
    def __init__(self, answers: dict[str, _Result]) -> None:
        self._answers = answers

    def analyze_module(
        self, module: str, _files: Any, _paths: Any, k: int = 10
    ) -> _Result:
        return self._answers[module]


class _Embedder:
    """Layer 4 fills the duplication corpus before searching it.

    Stubbed to a no-op: what is under test is how the results are reported, and
    the corpus-filling pass has its own coverage in
    tests/integration/test_layer4_incremental.py.
    """

    def __init__(self, _cache: Any) -> None:
        pass

    def embed_files(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}


@pytest.fixture()
def analyzer(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _install(answers: dict[str, _Result]) -> None:
        monkeypatch.setattr(
            "archguard.analysis.duplication.DuplicationAnalyzer",
            lambda _cache: _Analyzer(answers),
        )
        monkeypatch.setattr("archguard.analysis.semantic.SemanticAnalyzer", _Embedder)

    return _install


def _layer4(affected: dict[str, list[Path]]) -> tuple[float, list, str]:
    return _run_layer4(
        repo_root=Path("."),
        cache=None,
        contract=CONTRACT,
        affected=affected,
        commit_sha="abcdef1234",
    )


# ------------------------------------------------------------ 1. empty scope


def test_a_layer_with_no_modules_to_search_reports_that_it_did_not_run(
    analyzer: Any,
) -> None:
    """The regression. Nothing was searched, so 0.00 is not a result."""
    analyzer({})

    score, violations, skip_reason = _layer4({})

    assert skip_reason, (
        "Layer 4 searched no modules and reported no reason, so the caller "
        "will score its 0.00 as a clean pass"
    )
    assert "scope" in skip_reason.lower()
    assert score == 0.0
    assert violations == []


# ------------------------------------------------------- 2. measurable scope


def test_a_measured_module_means_the_layer_ran(analyzer: Any) -> None:
    analyzer({"alpha": _Result(score=0.0)})

    _score, _violations, skip_reason = _layer4({"alpha": []})

    assert skip_reason == "", (
        "a module that was searched and found clean must not be reported as "
        "unmeasured -- that is a real 0.00 and belongs in the score"
    )


def test_duplication_that_was_found_is_still_reported(analyzer: Any) -> None:
    """The layer's actual job, pinned so the reporting change cannot mute it."""
    analyzer({"alpha": _Result(score=0.7)})

    score, violations, skip_reason = _layer4({"alpha": []})

    assert skip_reason == ""
    assert score == pytest.approx(0.7)
    assert [v.module for v in violations] == ["alpha"]
    assert violations[0].layer == 4


def test_one_measured_module_is_enough_for_the_layer_to_count(analyzer: Any) -> None:
    """A signal from one module is not erased by another having nothing.

    This is also the half that used to be inverted: any single skipped module
    set the layer's reason, so a repository where nine modules were measured
    and one was not reported the whole layer as skipped.
    """
    analyzer(
        {
            "alpha": _Result(score=0.6),
            "beta": _Result(skipped=True, reason="no indexed functions for beta"),
        }
    )

    score, violations, skip_reason = _layer4({"alpha": [], "beta": []})

    assert skip_reason == ""
    assert score == pytest.approx(0.6)
    assert [v.module for v in violations] == ["alpha"]


def test_the_reason_does_not_depend_on_module_order(analyzer: Any) -> None:
    """`affected` is a dict, and the reason used to be whichever module came
    last -- so the same repository explained itself differently depending on
    iteration order."""
    answers = {
        "alpha": _Result(skipped=True, reason="first reason"),
        "beta": _Result(skipped=True, reason="second reason"),
    }
    analyzer(answers)
    forwards = _layer4({"alpha": [], "beta": []})[2]

    analyzer(answers)
    backwards = _layer4({"beta": [], "alpha": []})[2]

    assert forwards == "first reason"
    assert backwards == "second reason"
    # Each run reports the first module it actually saw, rather than the last,
    # so the message is a property of the scan instead of of dict ordering.
    assert forwards != backwards


# --------------------------------------------------------- 3. unavailability


def test_an_unavailable_layer_keeps_the_analyzer_s_own_explanation(
    analyzer: Any,
) -> None:
    """"No ML extras" and "nothing in scope" are different problems and send
    the reader somewhere different. The scope message must not overwrite one
    the analyzer already gave."""
    analyzer(
        {
            "alpha": _Result(
                skipped=True,
                reason='Layer 4 (duplication) skipped: install with pip install ".[ml]"',
            )
        }
    )

    _score, _violations, skip_reason = _layer4({"alpha": []})

    assert "install" in skip_reason
    assert "scope" not in skip_reason.lower()


def test_a_stale_cache_is_reported_as_itself(analyzer: Any) -> None:
    analyzer({"alpha": _Result(skipped=True, reason="Cache stale: centroid for alpha")})

    _score, _violations, skip_reason = _layer4({"alpha": []})

    assert "stale" in skip_reason.lower()


# ------------------------------------------------------- the Layer 3 rule too


def test_layer_3_and_layer_4_answer_the_same_question_the_same_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two runners are separate functions with one rule between them.

    They drifted once already -- Layer 3 reported skipped only when nothing was
    measured, Layer 4 whenever any module was -- and the drift is invisible
    until a repository hits the difference. Stated here so a change to one that
    is not made to the other fails.
    """
    from archguard.analysis._layer_runners import _run_layer3

    class _Drift:
        def __init__(self, skipped: bool, reason: str = "") -> None:
            self.drift_score = 0.0
            self.skipped = skipped
            self.skip_reason = reason

    class _Semantic:
        def __init__(self, _cache: Any) -> None:
            pass

        def compute_drift(self, module: str, _f: Any, _r: Any) -> _Drift:
            return _Drift(module == "beta", "nothing for beta")

        def embed_files(self, *_a: Any, **_k: Any) -> dict[str, Any]:
            return {}

    monkeypatch.setattr("archguard.analysis.semantic.SemanticAnalyzer", _Semantic)
    monkeypatch.setattr(
        "archguard.analysis.duplication.DuplicationAnalyzer",
        lambda _cache: _Analyzer(
            {
                "alpha": _Result(score=0.0),
                "beta": _Result(skipped=True, reason="nothing for beta"),
            }
        ),
    )

    # One module measured, one not, in both layers.
    l3_reason = _run_layer3(
        cache=None,
        contract={"modules": [{"name": "alpha"}, {"name": "beta"}]},
        affected={"alpha": [], "beta": []},
        py_files=[],
        commit_sha="abcdef1234",
        repo_root=Path("."),
    )[3]
    l4_reason = _layer4({"alpha": [], "beta": []})[2]

    assert l3_reason == l4_reason == "", (
        "the two layers disagree about whether a partially measured layer ran"
    )

    # And neither one measured.
    monkeypatch.setattr(
        "archguard.analysis.duplication.DuplicationAnalyzer",
        lambda _cache: _Analyzer({}),
    )
    assert bool(
        _run_layer3(
            cache=None,
            contract=CONTRACT,
            affected={},
            py_files=[],
            commit_sha="abcdef1234",
            repo_root=Path("."),
        )[3]
    ) is bool(_layer4({})[2]) is True
