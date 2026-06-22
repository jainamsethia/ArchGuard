"""Unit tests for the PR Risk Analyzer (Phase 4 Step 15)."""

from archguard.risk.pr_risk import PRRiskAnalyzer


def test_no_changed_files():
    analyzer = PRRiskAnalyzer()
    report = analyzer.analyze([], {"mod_a": ["src/mod_a"]})
    assert report.overall_risk == "none"
    assert report.risk_score == 0
    assert len(report.module_risks) == 0


def test_direct_module_identified():
    analyzer = PRRiskAnalyzer()
    report = analyzer.analyze(
        ["src/mod_a/foo.py", "src/mod_b/bar.py", "tests/foo_test.py"],
        {"mod_a": ["src/mod_a"], "mod_b": ["src/mod_b"]},
    )
    assert report.risk_score == 20  # 2 modules * 10
    assert report.overall_risk == "low"
    assert len(report.module_risks) == 2
    assert (
        report.module_risks[0].module == "mod_a"
        or report.module_risks[1].module == "mod_a"
    )
    assert report.module_risks[0].risk_level == "high"


def test_transitive_modules():
    analyzer = PRRiskAnalyzer()
    # mod_a changed. mod_b depends on mod_a. mod_c depends on mod_b.
    report = analyzer.analyze(
        ["src/mod_a/foo.py"],
        {"mod_a": ["src/mod_a"], "mod_b": ["src/mod_b"], "mod_c": ["src/mod_c"]},
        {"mod_b": ["mod_a"], "mod_c": ["mod_b"]},
    )
    assert report.risk_score == 14  # 1 direct (10) + 2 transitive (4)
    assert len(report.module_risks) == 3
    # mod_a should be high, others low
    high_risks = [m for m in report.module_risks if m.risk_level == "high"]
    assert len(high_risks) == 1
    assert high_risks[0].module == "mod_a"
    low_risks = [m for m in report.module_risks if m.risk_level == "low"]
    assert len(low_risks) == 2


def test_graceful_degradation_no_graph():
    analyzer = PRRiskAnalyzer()
    report = analyzer.analyze(["src/mod_a/foo.py"], {"mod_a": ["src/mod_a"]}, None)
    assert report.risk_score == 10
    assert len(report.module_risks) == 1


def test_risk_score_formula():
    analyzer = PRRiskAnalyzer()
    # 5 direct modules = 50. 5 transitive modules = 10. Total 60.
    changed_files = [f"src/mod_{i}/f.py" for i in range(5)]
    module_paths = {f"mod_{i}": [f"src/mod_{i}"] for i in range(10)}
    graph = {f"mod_{i}": [f"mod_{i - 5}"] for i in range(5, 10)}

    report = analyzer.analyze(changed_files, module_paths, graph)
    assert report.risk_score == 60


def test_classify_critical():
    analyzer = PRRiskAnalyzer()
    # > 10 direct modules -> score > 100 -> critical
    changed_files = [f"src/mod_{i}/f.py" for i in range(11)]
    module_paths = {f"mod_{i}": [f"src/mod_{i}"] for i in range(11)}
    report = analyzer.analyze(changed_files, module_paths)
    assert report.risk_score == 110
    assert report.overall_risk == "critical"


def test_classify_low():
    analyzer = PRRiskAnalyzer()
    report = analyzer.analyze(["src/mod_a/foo.py"], {"mod_a": ["src/mod_a"]})
    assert report.risk_score == 10
    assert report.overall_risk == "low"


def test_at_risk_capped_at_15():
    analyzer = PRRiskAnalyzer()
    changed_files = [f"src/mod_{i}/f.py" for i in range(20)]
    module_paths = {f"mod_{i}": [f"src/mod_{i}"] for i in range(20)}
    report = analyzer.analyze(changed_files, module_paths)
    assert report.risk_score == 200
    assert len(report.module_risks) == 15
