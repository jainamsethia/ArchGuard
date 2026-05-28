"""Tests for Severity classification."""

from archguard.utils.severity import Severity

def test_severity_values():
    assert Severity.CRITICAL.value == "critical"
    assert Severity.HIGH.value == "high"
    assert Severity.MEDIUM.value == "medium"
    assert Severity.LOW.value == "low"

def test_severity_ordering():
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    assert order[Severity.CRITICAL] < order[Severity.HIGH]
    assert order[Severity.HIGH] < order[Severity.MEDIUM]
    assert order[Severity.MEDIUM] < order[Severity.LOW]
