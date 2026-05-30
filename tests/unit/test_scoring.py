"""Unit tests for archguard.analysis.scoring."""

from __future__ import annotations

import pytest

from archguard.analysis.scoring import (
    ArchDebtBand,
    DEFAULT_WEIGHTS,
    LayerScores,
    calibrate_weights,
    classify_band,
    compute_archdebt,
)


class TestComputeArchdebt:
    """Tests for compute_archdebt."""

    def test_all_zeros_healthy(self) -> None:
        """All zeros -> HEALTHY, composite=0.0, should_fail_ci=False."""
        scores = LayerScores(0.0, 0.0, 0.0, 0.0)
        result = compute_archdebt(scores)
        assert result.band == ArchDebtBand.HEALTHY
        assert result.composite_score == 0.0
        assert result.should_fail_ci is False

    def test_composite_above_fail(self) -> None:
        """composite=0.76, fail_threshold=0.75 -> CRITICAL."""
        scores = LayerScores(0.76, 0.76, 0.76, 0.76)
        result = compute_archdebt(scores, fail_threshold=0.75)
        assert result.band == ArchDebtBand.CRITICAL
        assert result.composite_breach is True

    def test_watch_band(self) -> None:
        """composite=0.60, warn=0.50, fail=0.75 -> WARN."""
        scores = LayerScores(0.60, 0.60, 0.60, 0.60)
        result = compute_archdebt(
            scores,
            warn_threshold=0.50,
            fail_threshold=0.75,
        )
        assert result.band == ArchDebtBand.WARN

    def test_per_component_breach(self) -> None:
        """layer1=0.90, composite=0.30 -> per_component_breach, should_fail_ci."""
        scores = LayerScores(0.90, 0.10, 0.10, 0.10)
        result = compute_archdebt(scores, fail_threshold=0.75)
        assert result.per_component_breach is True
        assert result.should_fail_ci is True

    def test_both_breaches(self) -> None:
        """Both composite + per-component breach -> both flags True."""
        scores = LayerScores(0.95, 0.95, 0.95, 0.95)
        result = compute_archdebt(scores, fail_threshold=0.75)
        assert result.composite_breach is True
        assert result.per_component_breach is True
        assert result.should_fail_ci is True
        assert len(result.fail_reasons) >= 2


class TestCalibrateWeights:
    """Tests for calibrate_weights."""

    def test_empty_returns_default(self) -> None:
        """Empty input -> DEFAULT_WEIGHTS."""
        result = calibrate_weights([], [])
        assert result == DEFAULT_WEIGHTS

    def test_valid_history_sums_to_one(self) -> None:
        """Valid history -> weights sum to 1.0, all non-negative."""
        history = [
            LayerScores(0.5, 0.3, 0.2, 0.4),
            LayerScores(0.3, 0.5, 0.4, 0.2),
            LayerScores(0.4, 0.4, 0.3, 0.3),
        ]
        targets = [0.5, 0.4, 0.35]
        result = calibrate_weights(history, targets)
        assert abs(sum(result) - 1.0) < 1e-6
        assert all(w >= 0.0 for w in result)


class TestClassifyBand:
    """Tests for classify_band."""

    def test_healthy(self) -> None:
        assert classify_band(0.24, 0.50, 0.75) == ArchDebtBand.HEALTHY

    def test_warn_boundary(self) -> None:
        """warn_threshold is inclusive."""
        assert classify_band(0.50, 0.50, 0.75) == ArchDebtBand.WARN

    def test_watch_boundary(self) -> None:
        """warn_threshold / 2 is inclusive."""
        assert classify_band(0.25, 0.50, 0.75) == ArchDebtBand.WATCH

    def test_critical_boundary(self) -> None:
        """fail_threshold is inclusive."""
        assert classify_band(0.75, 0.50, 0.75) == ArchDebtBand.CRITICAL


def test_compute_archdebt_no_ml_deps(monkeypatch):
    """compute_archdebt must work even if numpy/scipy are not importable."""
    import sys

    # Simulate numpy not being installed
    monkeypatch.setitem(sys.modules, "numpy", None)
    monkeypatch.setitem(sys.modules, "scipy", None)
    from archguard.analysis.scoring import compute_archdebt, LayerScores

    result = compute_archdebt(LayerScores(0.3, 0.5, 0.2, 0.4), [1.0, 1.0, 1.0, 1.0])
    assert result.composite_score == pytest.approx(0.35, abs=0.01)


def test_compute_archdebt_clamping():
    from archguard.analysis.scoring import compute_archdebt, LayerScores

    result = compute_archdebt(
        LayerScores(2.0, 2.0, 2.0, 2.0), [1.0, 1.0, 1.0, 1.0]
    )  # over 1.0
    assert result.composite_score == 1.0
