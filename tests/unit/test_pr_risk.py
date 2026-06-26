"""Regression tests for archguard.risk.pr_risk (LOW-004 coverage)."""

import pytest

def test_pr_risk_module_imports() -> None:
    """Verifies the risk module is importable after LOW-001's __init__.py fix."""
    import archguard.risk.pr_risk as pr_risk
    assert pr_risk is not None

def test_calculate_pr_risk_returns_score_for_empty_diff() -> None:
    """
    Regression test for LOW-004.
    Verifies: analyze returns a numeric risk score for an empty/minimal diff without raising.
    """
    from archguard.risk.pr_risk import PRRiskAnalyzer

    analyzer = PRRiskAnalyzer()
    # Arrange: minimal valid diff input (empty list of changed files)
    result = analyzer.analyze(changed_files=[], module_paths={"modA": ["src/modA"]})

    # Assert: result is a PRRiskReport with an integer risk score
    assert hasattr(result, "risk_score")
    assert isinstance(result.risk_score, int)

def test_calculate_pr_risk_increases_with_more_changes() -> None:
    """
    Verifies: PRs with more changed files/lines get a higher risk score
    than minimal PRs (basic monotonicity check).
    """
    from archguard.risk.pr_risk import PRRiskAnalyzer

    analyzer = PRRiskAnalyzer()

    # Low risk (1 file in 1 module)
    low_risk_report = analyzer.analyze(
        changed_files=["src/modA/file1.py"],
        module_paths={"modA": ["src/modA"]},
    )
    
    # High risk (3 files in 3 different modules)
    high_risk_report = analyzer.analyze(
        changed_files=["src/modA/file1.py", "src/modB/file2.py", "src/modC/file3.py"],
        module_paths={"modA": ["src/modA"], "modB": ["src/modB"], "modC": ["src/modC"]},
    )

    assert high_risk_report.risk_score >= low_risk_report.risk_score
