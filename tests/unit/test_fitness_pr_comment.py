from archguard.github.comments import build_fitness_section


def test_build_fitness_section_empty():
    assert build_fitness_section([]) == ""


def test_build_fitness_section_all_pass():
    results = [
        {"name": "Rule 1", "rule": "rule1", "passed": True},
        {"name": "Rule 2", "rule": "rule2", "passed": True},
    ]
    markdown = build_fitness_section(results)

    assert "## Architecture Fitness Functions" in markdown
    assert "✅ All 2 fitness function(s) passed." in markdown
    assert "✅ Passing: Rule 1, Rule 2" in markdown
    assert "### ⛔ Critical Failures" not in markdown
    assert "### ⚠️ Warnings" not in markdown


def test_build_fitness_section_all_pass_truncation():
    results = [
        {"name": f"Rule {i}", "rule": f"rule{i}", "passed": True} for i in range(1, 8)
    ]
    markdown = build_fitness_section(results)

    assert "✅ All 7 fitness function(s) passed." in markdown
    assert "✅ Passing: Rule 1, Rule 2, Rule 3, Rule 4, Rule 5 (+2 more)" in markdown


def test_build_fitness_section_critical_failure():
    results = [
        {
            "name": "Crit Rule",
            "rule": "rule_crit",
            "passed": False,
            "severity": "critical",
            "evidence": "bad",
        },
        {"name": "Pass Rule", "rule": "rule_pass", "passed": True},
    ]
    markdown = build_fitness_section(results)

    assert "❌ 1 of 2 fitness function(s) failed." in markdown
    assert "### ⛔ Critical Failures" in markdown
    assert "| Crit Rule | rule_crit | bad |" in markdown
    assert "### ⚠️ Warnings" not in markdown
    assert "✅ Passing: Pass Rule" in markdown


def test_build_fitness_section_warn_failure():
    results = [
        {
            "name": "Warn Rule",
            "rule": "rule_warn",
            "passed": False,
            "severity": "warn",
            "evidence": "watch out",
        },
    ]
    markdown = build_fitness_section(results)

    assert "❌ 1 of 1 fitness function(s) failed." in markdown
    assert "### ⚠️ Warnings" in markdown
    assert "| Warn Rule | rule_warn | watch out |" in markdown
    assert "### ⛔ Critical Failures" not in markdown
    assert "✅ Passing:" not in markdown
