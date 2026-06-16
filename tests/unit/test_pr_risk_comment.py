"""Unit tests for the PR risk section formatting (Phase 4 Step 16)."""

from typing import Any
from dataclasses import dataclass, field

from archguard.github.comments import build_risk_section


@dataclass
class DummyModuleRisk:
    module: str
    risk_level: str
    reasons: list[str] = field(default_factory=list)


@dataclass
class DummyRiskReport:
    overall_risk: str
    module_risks: list[DummyModuleRisk] = field(default_factory=list)


def test_build_risk_section_none():
    """Test that a report with 'none' risk returns an empty string."""
    report = DummyRiskReport(overall_risk="none")
    output = build_risk_section(report)
    assert output == ""

    # Should also handle None gracefully
    assert build_risk_section(None) == ""


def test_build_risk_section_critical():
    """Test that critical overall risk is formatted correctly."""
    report = DummyRiskReport(overall_risk="critical")
    output = build_risk_section(report)
    
    assert "## Risk Assessment" in output
    assert "**Overall Risk**: 🚨 Critical" in output


def test_build_risk_section_module_table():
    """Test that the module risk table is formatted correctly."""
    report = DummyRiskReport(
        overall_risk="high",
        module_risks=[
            DummyModuleRisk(module="auth", risk_level="high", reasons=["Heavy coupling", "Security rule failed"]),
            DummyModuleRisk(module="utils", risk_level="low", reasons=["Minor duplication"]),
            DummyModuleRisk(module="core", risk_level="medium", reasons=[]),
        ]
    )
    output = build_risk_section(report)
    
    assert "### Module Risk Table" in output
    assert "| Module | Risk Level | Details |" in output
    assert "| auth | High | Heavy coupling, Security rule failed |" in output
    assert "| utils | Low | Minor duplication |" in output
    assert "| core | Medium | No specific reasons provided. |" in output


def test_module_table_capped_at_10():
    """Test that the module table does not display more than 10 modules."""
    module_risks = [
        DummyModuleRisk(module=f"mod_{i}", risk_level="medium", reasons=["Reason"])
        for i in range(15)
    ]
    report = DummyRiskReport(overall_risk="medium", module_risks=module_risks)
    output = build_risk_section(report)
    
    assert "### Module Risk Table" in output
    
    # Check that mod_0 through mod_9 are present
    for i in range(10):
        assert f"| mod_{i} | Medium | Reason |" in output
        
    # Check that mod_10 and beyond are NOT present
    for i in range(10, 15):
        assert f"| mod_{i} | Medium | Reason |" not in output
        
    # Check that the truncation message is present
    assert "*(+5 more modules hidden)*" in output
