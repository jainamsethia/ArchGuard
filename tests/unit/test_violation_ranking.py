"""Tests for deterministic violation ranking and selection.

The ordering these pin is the replacement for a blind ``violations[:15]`` slice,
which took whichever fifteen the layers happened to append first.
"""

from __future__ import annotations

import random

from archguard.analysis import violation_kinds
from archguard.analysis.ranking import (
    DEFAULT_REMEDIATION_LIMIT,
    finding_key,
    natural_metric,
    rank_violations,
    select_for_remediation,
)


def _v(severity, kind, module="m", message="msg", metrics=None, layer=2, file=""):
    return {
        "severity": severity,
        "kind": kind,
        "module": module,
        "message": message,
        "metrics": metrics or {},
        "layer": layer,
        "file": file,
    }


def _fan_out(module, fan_out, budget=10, **kw):
    return _v(
        "high",
        violation_kinds.FAN_OUT,
        module=module,
        message=f"fan_out={fan_out} exceeds budget={budget}",
        metrics={"fan_out": float(fan_out), "budget": float(budget)},
        **kw,
    )


# ---------------------------------------------------------------------------
# Tier ordering
# ---------------------------------------------------------------------------


def test_failed_critical_gate_outranks_everything():
    """The cycle gate is its own top tier, above ordinary CRITICAL.

    Consistent with scoring.apply_fitness_results, where a failed critical gate
    caps the reported grade regardless of the composite.
    """
    violations = [_v("critical", violation_kinds.IMPORT_BOUNDARY, module="a")]
    gates = [
        {
            "name": "no_circular_deps",
            "rule": "graph.cycles == 0",
            "passed": False,
            "severity": "critical",
            "evidence": "Cycle found: lib -> extra -> lib",
        }
    ]

    sel = select_for_remediation(violations, fitness_results=gates)

    assert sel.selected[0].is_fitness_gate is True
    assert sel.selected[0].kind == violation_kinds.DEPENDENCY_CYCLE
    assert sel.selected[1].finding["severity"] == "critical"


def test_passing_and_non_critical_gates_are_not_ranked():
    gates = [
        {"name": "passes", "passed": True, "severity": "critical", "evidence": ""},
        {"name": "warn_only", "passed": False, "severity": "warn", "evidence": "x"},
    ]
    sel = select_for_remediation([], fitness_results=gates)
    assert sel.selected == []


def test_severity_tiers_order_worst_first():
    violations = [
        _v("low", violation_kinds.DUPLICATION, module="d"),
        _v("critical", violation_kinds.IMPORT_BOUNDARY, module="a"),
        _v("medium", violation_kinds.DUPLICATION, module="c"),
        _v("high", violation_kinds.FAN_OUT, module="b"),
    ]
    ordered = [r.finding["severity"] for r in rank_violations(violations)]
    assert ordered == ["critical", "high", "medium", "low"]


def test_unknown_severity_sorts_last_rather_than_crashing():
    violations = [
        _v("banana", violation_kinds.DUPLICATION, module="z"),
        _v("low", violation_kinds.DUPLICATION, module="a"),
    ]
    ordered = [r.finding["severity"] for r in rank_violations(violations)]
    assert ordered == ["low", "banana"]


# ---------------------------------------------------------------------------
# Within-tier ordering uses each kind's own metric
# ---------------------------------------------------------------------------


def test_fan_out_orders_by_percent_over_budget_not_raw_fan_out():
    """A module 120% over a small budget outranks one 10% over a large one."""
    violations = [
        _fan_out("wide", fan_out=22, budget=20),  # 10% over
        _fan_out("tight", fan_out=11, budget=5),  # 120% over
    ]
    ordered = [r.finding["module"] for r in rank_violations(violations)]
    assert ordered == ["tight", "wide"]


def test_duplication_orders_by_duplication_score():
    violations = [
        _v("medium", violation_kinds.DUPLICATION, module="a",
           metrics={"duplication_score": 0.10}),
        _v("medium", violation_kinds.DUPLICATION, module="b",
           metrics={"duplication_score": 0.42}),
    ]
    assert [r.finding["module"] for r in rank_violations(violations)] == ["b", "a"]


def test_metrics_of_different_kinds_are_never_compared():
    """Kind groups within a tier, so 0.9 'drift' never outranks 0.1 'duplication'
    merely because 0.9 > 0.1 -- they are not the same quantity."""
    violations = [
        _v("medium", violation_kinds.SEMANTIC_DRIFT, module="s", metrics={"drift": 0.9}),
        _v("medium", violation_kinds.DUPLICATION, module="d",
           metrics={"duplication_score": 0.1}),
    ]
    kinds = [r.kind for r in rank_violations(violations)]
    # duplication < semantic_drift alphabetically; the point is that the grouping
    # is by kind, not by raw magnitude across kinds.
    assert kinds == [violation_kinds.DUPLICATION, violation_kinds.SEMANTIC_DRIFT]


def test_kinds_without_a_magnitude_get_zero():
    assert natural_metric(violation_kinds.IMPORT_BOUNDARY, {}) == 0.0
    assert natural_metric("something_new", {"whatever": 5.0}) == 0.0


def test_zero_budget_does_not_divide_by_zero():
    assert natural_metric(
        violation_kinds.FAN_OUT, {"fan_out": 7.0, "budget": 0.0}
    ) == 7.0


# ---------------------------------------------------------------------------
# Determinism, including exact ties
# ---------------------------------------------------------------------------


def _tied_violations(n: int) -> list[dict]:
    """n violations identical in tier, kind and metric -- only the path differs.

    Deliberately harsher than any real repository: it forces the alphabetical
    tiebreaker to carry the entire ordering.
    """
    return [
        _fan_out(f"module_{i:02d}", fan_out=15, budget=10, file=f"src/mod_{i:02d}.py")
        for i in range(n)
    ]


def test_exact_ties_fall_back_to_path_order():
    violations = _tied_violations(20)
    ordered = [r.finding["module"] for r in rank_violations(violations)]
    assert ordered == sorted(ordered)


def test_selection_is_identical_across_input_orderings():
    """Same findings in any order -> same selected set, every run."""
    violations = _tied_violations(25)
    baseline = [
        finding_key(r.finding) for r in select_for_remediation(violations).selected
    ]

    for seed in range(8):
        shuffled = violations[:]
        random.Random(seed).shuffle(shuffled)
        got = [
            finding_key(r.finding) for r in select_for_remediation(shuffled).selected
        ]
        assert got == baseline, f"selection changed for input order seed={seed}"


def test_cap_applies_and_leaves_the_rest_unselected():
    violations = _tied_violations(25)
    sel = select_for_remediation(violations)

    assert sel.detected_count == 25
    assert sel.eligible_count == 25
    assert sel.selected_count == DEFAULT_REMEDIATION_LIMIT
    assert len(sel.selected) < sel.detected_count


def test_higher_severity_is_never_crowded_out_by_arrival_order():
    """The regression the blind slice caused: LOW findings first in the list
    used to consume the whole budget."""
    violations = [
        _v("low", violation_kinds.DUPLICATION, module=f"low_{i:02d}",
           metrics={"duplication_score": 0.5})
        for i in range(20)
    ] + [_fan_out("important", fan_out=30)]

    sel = select_for_remediation(violations)

    assert sel.selected[0].finding["module"] == "important"


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------


def test_suppressed_violations_are_excluded_from_selection_and_counted():
    violations = _tied_violations(5)
    suppressed_modules = {"module_01", "module_03"}

    sel = select_for_remediation(
        violations,
        is_suppressed=lambda v: v["module"] in suppressed_modules,
    )

    assert sel.detected_count == 5
    assert sel.suppressed_count == 2
    assert sel.eligible_count == 3
    picked = {r.finding["module"] for r in sel.selected}
    assert picked.isdisjoint(suppressed_modules)


def test_suppressed_violations_do_not_consume_cap_slots():
    """A suppressed finding must not occupy a slot a real finding could use."""
    violations = _tied_violations(DEFAULT_REMEDIATION_LIMIT + 5)
    # Suppress the alphabetically-first ones, which would otherwise be selected.
    suppressed = {f"module_{i:02d}" for i in range(5)}

    sel = select_for_remediation(
        violations, is_suppressed=lambda v: v["module"] in suppressed
    )

    assert sel.selected_count == DEFAULT_REMEDIATION_LIMIT
    assert {r.finding["module"] for r in sel.selected}.isdisjoint(suppressed)


def test_finding_key_matches_the_suppression_identity_triple():
    v = _v("high", violation_kinds.FAN_OUT, module="m", message="msg", layer=2)
    assert finding_key(v) == "m|2|msg"
