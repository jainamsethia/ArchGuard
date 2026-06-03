import pytest
from archguard.analysis.coupling import analyze_coupling, compute_coupling_delta
from archguard.analysis.parser import ImportEdge

def test_analyze_coupling_empty_graph():
    # Empty inputs
    result = analyze_coupling([], {}, {})
    assert len(result) == 0

def test_analyze_coupling_simple_cycle():
    # A -> B, B -> A
    edges = [
        ImportEdge(source_file="m1/a.py", imported_module="m2", is_stdlib=False, is_third_party=False, is_relative=False),
        ImportEdge(source_file="m2/b.py", imported_module="m1", is_stdlib=False, is_third_party=False, is_relative=False)
    ]
    module_paths = {"m1": ["m1"], "m2": ["m2"]}
    budgets = {"m1": 1, "m2": 1}
    
    result = analyze_coupling(edges, module_paths, budgets)
    assert len(result) == 2

def test_analyze_coupling_single_node_no_edges():
    module_paths = {"m1": ["m1"]}
    result = analyze_coupling([], module_paths, {"m1": 1})
    assert len(result) == 1
    assert result[0].module_name == "m1"
    assert result[0].fan_out == 0

def test_assign_file_to_module_test_paths():
    # Should skip tests paths (implicitly called by compute_fan_in)
    edges = [
        ImportEdge(source_file="tests/m1/a.py", imported_module="m2", is_stdlib=False, is_third_party=False, is_relative=False),
    ]
    module_paths = {"m1": ["tests/m1"], "m2": ["m2"]}
    result = analyze_coupling(edges, module_paths, {})
    assert len(result) == 2

def test_assign_file_to_module_unassigned():
    # Should hit unassigned warning branch
    edges = [
        ImportEdge(source_file="unknown/a.py", imported_module="m2", is_stdlib=False, is_third_party=False, is_relative=False),
    ]
    module_paths = {"m2": ["m2"]}
    result = analyze_coupling(edges, module_paths, {})
    assert len(result) == 1

def test_compute_coupling_delta_zero_budget():
    delta = compute_coupling_delta(0, 0, "m1")
    assert delta == 1.0

def test_compute_coupling_delta_exceeds():
    delta = compute_coupling_delta(5, 2, "m1")
    assert delta == min(1.0, (5 - 2) / 2)
