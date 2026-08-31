"""Layer 3 has to say whether it measured anything, and mean it.

`_run_layer3` reports "did I run?" out of band, as an empty skip reason. That
works for the two cases it was written for -- measured cleanly, or every module
had no baseline -- and silently fails for the third: no modules at all. The
loop never runs, the reason stays empty, and the caller reads an empty reason
as "this layer ran and found nothing", scoring a 0.00 that was never measured
into the composite.

An incremental scan reaches that third case routinely. Its `affected` is built
from the files that changed, and an auto-generated contract only names the
modules it could measure -- so an edit anywhere else produces an empty module
map, while a full scan of the same tree hands Layer 3 every module and reports
it skipped. Same repository, two different scores, which is exactly what
incremental analysis is not allowed to do.

The end-to-end consequence is
tests/integration/test_incremental_layer_scope.py. This states the rule itself,
without a database or a model, so a future edit that reintroduces it fails in a
second and says why.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from archguard.analysis._layer_runners import _run_layer3

CONTRACT: dict[str, Any] = {
    "modules": [
        {"name": "alpha", "path": "alpha/", "semantic_drift_threshold": 0.25},
        {"name": "beta", "path": "beta/", "semantic_drift_threshold": 0.25},
    ]
}


class _Drift:
    """One module's answer, in the shape `compute_drift` returns."""

    def __init__(self, score: float, skipped: bool = False, reason: str = "") -> None:
        self.drift_score = score
        self.skipped = skipped
        self.skip_reason = reason


class _Analyzer:
    def __init__(self, answers: dict[str, _Drift]) -> None:
        self._answers = answers

    def compute_drift(self, module: str, _files: Any, _root: Any) -> _Drift:
        return self._answers[module]


@pytest.fixture()
def analyzer(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Stand in for the embedding model.

    Mocked here on purpose: what is under test is how the layer *reports* the
    answers it got, not the answers. The real model runs against the real
    pipeline in the integration test named above.
    """

    def _install(answers: dict[str, _Drift]) -> None:
        monkeypatch.setattr(
            "archguard.analysis.semantic.SemanticAnalyzer",
            lambda _cache: _Analyzer(answers),
        )

    return _install


def _layer3(affected: dict[str, list[Path]]) -> tuple[float, dict, list, str]:
    return _run_layer3(
        cache=None,
        contract=CONTRACT,
        affected=affected,
        py_files=[],
        commit_sha="abcdef1234",
        repo_root=Path("."),
    )


def test_a_layer_with_no_modules_to_measure_reports_that_it_did_not_run(
    analyzer: Any,
) -> None:
    """The regression.

    Nothing was looked at, so 0.00 is not a result. Reported as measured, it is
    averaged into the health score as a passing layer.
    """
    analyzer({})

    _drift, _scores, violations, skip_reason = _layer3({})

    assert skip_reason, (
        "Layer 3 measured no modules and reported no reason, so the caller will "
        "score its 0.00 as a clean pass"
    )
    assert violations == []


def test_a_measured_module_means_the_layer_ran(analyzer: Any) -> None:
    analyzer({"alpha": _Drift(0.1)})

    _drift, _scores, _violations, skip_reason = _layer3({"alpha": []})

    assert skip_reason == ""


def test_a_module_that_could_not_be_measured_explains_why(analyzer: Any) -> None:
    analyzer({"alpha": _Drift(0.0, skipped=True, reason="no prior baseline")})

    _drift, _scores, _violations, skip_reason = _layer3({"alpha": []})

    assert skip_reason == "no prior baseline"


def test_one_measured_module_is_enough_for_the_layer_to_count(analyzer: Any) -> None:
    """The existing rule, pinned so the fix above does not overreach.

    A layer that measured some modules produced a real signal. Calling it
    skipped because another module had no baseline would drop that signal out
    of the score entirely.
    """
    analyzer(
        {
            "alpha": _Drift(0.4),
            "beta": _Drift(0.0, skipped=True, reason="no prior baseline"),
        }
    )

    drift, _scores, violations, skip_reason = _layer3({"alpha": [], "beta": []})

    assert skip_reason == ""
    assert drift == pytest.approx(0.4)
    assert [v.module for v in violations] == ["alpha"]


def test_the_reason_names_the_scope_rather_than_the_model(analyzer: Any) -> None:
    """Whoever reads this is looking at a layer marked skipped on a scan that
    had ML available and working. "Install the extras" would send them after
    the wrong thing."""
    analyzer({})

    _drift, _scores, _violations, skip_reason = _layer3({})

    assert "scope" in skip_reason.lower()
    assert "install" not in skip_reason.lower()
