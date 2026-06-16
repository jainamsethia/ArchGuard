import pytest
from pathlib import Path

from archguard.analysis._models import AnalysisResult, ViolationDetail
from archguard.analysis.scoring import ArchDebtResult, ArchDebtBand, LayerScores
from archguard.fitness.evaluator import FitnessFunctionEvaluator
from archguard.fitness.result import FitnessFunctionResult
from archguard.utils.severity import Severity
from archguard.analysis.parser import ImportEdge

def _mock_analysis_result(violations=None, health_score=100.0, layer_scores=None):
    if layer_scores is None:
        layer_scores = LayerScores(0.0, 0.0, 0.0, 0.0)
    
    composite = 1.0 - (health_score / 100.0)
    
    archdebt = ArchDebtResult(
        composite_score=composite,
        band=ArchDebtBand.HEALTHY,
        layer_scores=layer_scores,
        weights=(0.25, 0.25, 0.25, 0.25),
        per_component_breach=False,
        composite_breach=False,
        should_fail_ci=False
    )
    
    return AnalysisResult(
        archdebt=archdebt,
        violations=violations or [],
        layer_scores=layer_scores
    )

def _setup_mock_evaluator() -> FitnessFunctionEvaluator:
    # Contract with 'core' and 'api'
    contract = {
        "modules": [
            {"name": "core", "path": "src/core", "coupling_budget": 3},
            {"name": "api", "path": "src/api", "coupling_budget": 3}
        ]
    }
    evaluator = FitnessFunctionEvaluator(repo_root=Path("."), contract=contract)
    # Mock the graph to prevent parsing the filesystem during unit tests
    evaluator._graph_prepared = True
    evaluator._module_paths = {"core": ["src/core"], "api": ["src/api"]}
    evaluator._parsed_edges = []
    return evaluator

def test_module_import_rule_passes():
    evaluator = _setup_mock_evaluator()
    result = _mock_analysis_result()
    out = evaluator.evaluate(result, ["module[core] must not import module[api]"])
    assert len(out) == 1
    assert out[0].passed is True

def test_module_import_rule_fails():
    evaluator = _setup_mock_evaluator()
    evaluator._parsed_edges = [
        ImportEdge(source_file="src/core/main.py", imported_module="src.api.models", is_stdlib=False, is_third_party=False, is_relative=False)
    ]
    result = _mock_analysis_result()
    out = evaluator.evaluate(result, ["module[core] must not import module[api]"])
    assert len(out) == 1
    assert out[0].passed is False

def test_no_cycles_empty_graph():
    evaluator = _setup_mock_evaluator()
    result = _mock_analysis_result()
    out = evaluator.evaluate(result, ["graph.cycles == 0"])
    assert len(out) == 1
    assert out[0].passed is True

def test_no_cycles_with_cycle():
    evaluator = _setup_mock_evaluator()
    evaluator._parsed_edges = [
        ImportEdge(source_file="src/core/a.py", imported_module="src.api.b", is_stdlib=False, is_third_party=False, is_relative=False),
        ImportEdge(source_file="src/api/b.py", imported_module="src.core.a", is_stdlib=False, is_third_party=False, is_relative=False)
    ]
    result = _mock_analysis_result()
    out = evaluator.evaluate(result, ["graph.cycles == 0"])
    assert out[0].passed is False
    assert "Cycle found" in out[0].details

def test_fan_out_exceeded():
    evaluator = _setup_mock_evaluator()
    # 5 different imports to exceed fan_out 4
    evaluator._parsed_edges = [
        ImportEdge(source_file="src/api/a.py", imported_module="ext1", is_stdlib=False, is_third_party=True, is_relative=False),
        ImportEdge(source_file="src/api/a.py", imported_module="ext2", is_stdlib=False, is_third_party=True, is_relative=False),
        ImportEdge(source_file="src/api/a.py", imported_module="ext3", is_stdlib=False, is_third_party=True, is_relative=False),
        ImportEdge(source_file="src/api/a.py", imported_module="ext4", is_stdlib=False, is_third_party=True, is_relative=False),
        ImportEdge(source_file="src/api/a.py", imported_module="ext5", is_stdlib=False, is_third_party=True, is_relative=False),
    ]
    result = _mock_analysis_result()
    out = evaluator.evaluate(result, ["module[api].fan_out <= 4"])
    assert out[0].passed is False
    assert "fan_out=5" in out[0].details

def test_health_score_gate_pass():
    evaluator = _setup_mock_evaluator()
    result = _mock_analysis_result(health_score=85.0)
    out = evaluator.evaluate(result, ["health_score >= 80"])
    assert out[0].passed is True

def test_health_score_gate_fail():
    evaluator = _setup_mock_evaluator()
    result = _mock_analysis_result(health_score=75.0)
    out = evaluator.evaluate(result, ["health_score >= 80"])
    assert out[0].passed is False

def test_layer_debt_threshold():
    evaluator = _setup_mock_evaluator()
    layer_scores = LayerScores(0.5, 0.2, 0.1, 0.0)
    result = _mock_analysis_result(layer_scores=layer_scores)
    out = evaluator.evaluate(result, ["layer[1].debt <= 0.4"])
    assert out[0].passed is False

def test_unknown_rule_syntax():
    evaluator = _setup_mock_evaluator()
    result = _mock_analysis_result()
    out = evaluator.evaluate(result, ["invalid rule here"])
    assert out[0].passed is False
    assert "Unknown rule syntax." == out[0].error

def test_evaluate_multiple_rules():
    evaluator = _setup_mock_evaluator()
    result = _mock_analysis_result(health_score=90.0)
    rules = [
        "health_score >= 80",
        "graph.cycles == 0"
    ]
    out = evaluator.evaluate(result, rules)
    assert len(out) == 2
    assert all(r.passed for r in out)
