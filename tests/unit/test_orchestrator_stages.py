"""Unit tests for archguard.analysis._orchestrator_stages."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from archguard.analysis.layers import ViolationDetail
from archguard.analysis._orchestrator_stages import _run_layer_3
from archguard.analysis._orchestrator_run import _finalize_result


def test_run_layer_3_propagates_missing_ml_runtime_error(monkeypatch):
    """Layer 3's RuntimeError-on-missing-ML-deps handling must propagate
    the exception, exactly as the original inline try/except did."""

    # Ensure ARCHGUARD_SKIP_ML is unset so layer 3 actually runs
    monkeypatch.delenv("ARCHGUARD_SKIP_ML", raising=False)

    # Mock orchestrator
    orchestrator = MagicMock()
    orchestrator.contract = {}

    # Mock _run_layer3 to raise the expected RuntimeError
    def mock_run_layer3(*args, **kwargs):
        raise RuntimeError("ML dependencies are not installed")

    monkeypatch.setattr(
        "archguard.analysis._layer_runners._run_layer3", mock_run_layer3
    )

    violations_in = [
        ViolationDetail(
            layer=1, module="test", message="msg", commit_sha="abcd", file_path="a.py"
        )
    ]

    # Mock metrics context manager
    class DummyMetrics:
        def time_layer(self, layer):
            class Context:
                def __enter__(self):
                    pass

                def __exit__(self, exc_type, exc_val, exc_tb):
                    pass

            return Context()

    with pytest.raises(RuntimeError, match="ML dependencies are not installed"):
        _run_layer_3(
            orchestrator=orchestrator,
            py_files=[Path("a.py")],
            violations=violations_in.copy(),
            affected=[],
            progress=None,
            quiet=True,
            fail_fast=False,
            evaluate_fitness=lambda res: None,
            metrics=DummyMetrics(),
            commit_sha="abcd",
            rel_files=["a.py"],
            layer1=0.0,
            layer2=0.0,
            unique_failures=[],
        )


def test_finalize_result_filters_suppressed_violations(monkeypatch):
    """_finalize_result must drop violations matching the contract's suppress
    list before computing the final ArchDebt score."""

    orchestrator = MagicMock()
    orchestrator.repo_root = Path("/fake/repo")
    orchestrator.contract = {}

    # Mock _filter_suppressed to drop one of the violations
    v1 = ViolationDetail(
        layer=1, module="test", message="msg1", commit_sha="abcd", file_path="a.py"
    )
    v2 = ViolationDetail(
        layer=2, module="test", message="msg2", commit_sha="abcd", file_path="b.py"
    )

    def mock_filter(repo_root, viols):
        return [v for v in viols if v.message != "msg1"]

    monkeypatch.setattr(
        "archguard.analysis._orchestrator_run._filter_suppressed_fn", mock_filter
    )

    class DummyMetrics:
        def __init__(self):
            self.extra = {}

        def to_dict(self):
            return {}

    res = _finalize_result(
        orchestrator=orchestrator,
        violations=[v1, v2],
        commit_sha="abcd",
        metrics=DummyMetrics(),
        evaluate_fitness=lambda res: None,
        layer1=0.0,
        layer2=0.0,
        layer3=0.0,
        layer4=0.0,
        affected=["a.py", "b.py"],
        rel_files=["a.py", "b.py"],
        unique_failures=[],
    )

    # The filtered result should only contain V2
    assert len(res.violations) == 1
    assert res.violations[0].message == "msg2"
