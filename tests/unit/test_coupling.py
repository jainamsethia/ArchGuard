"""Unit tests for archguard.analysis.coupling."""

from __future__ import annotations

import logging

import pytest

from archguard.analysis.coupling import compute_coupling_delta, default_coupling_budget


class TestCouplingDelta:
    """Tests for compute_coupling_delta()."""

    def test_zero_zero_returns_one_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """fan_out=0, coupling_budget=0 → coupling_delta=1.0 with warning."""
        with caplog.at_level(logging.WARNING):
            result = compute_coupling_delta(0, 0)
        assert result == 1.0
        assert "CouplingDelta set to 1.0" in caplog.text

    def test_under_budget(self) -> None:
        """fan_out=3, coupling_budget=5 → coupling_delta=0.0."""
        assert compute_coupling_delta(3, 5) == 0.0

    def test_over_budget(self) -> None:
        """fan_out=8, coupling_budget=5 → coupling_delta=0.6."""
        result = compute_coupling_delta(8, 5)
        assert result == pytest.approx(0.6)


class TestDefaultCouplingBudget:
    """Tests for default_coupling_budget()."""

    def test_normal_value(self) -> None:
        """default_coupling_budget(4) → 6  (ceil(4*1.5) = 6)."""
        assert default_coupling_budget(4) == 6

    def test_minimum_clamp(self) -> None:
        """default_coupling_budget(1) → 3  (max(3, ceil(1*1.5)) = 3)."""
        assert default_coupling_budget(1) == 3
