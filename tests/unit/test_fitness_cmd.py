"""Tests for archguard fitness check CLI command."""

import json

import pytest
from typer.testing import CliRunner

import archguard.cli.fitness_cmd
from archguard.cli.main import app
from archguard.config import FitnessFunctionConfig
from archguard.fitness.result import FitnessFunctionResult
from tests.conftest import strip_rich

runner = CliRunner()


@pytest.fixture
def mock_pipeline(monkeypatch):
    """Mocks out the AnalysisOrchestrator and load_contract to avoid running full analysis."""

    class MockOrchestrator:
        def __init__(self, repo):
            self.repo = repo

        def run(self, *args, **kwargs):
            class MockAnalysisResult:
                pass

            return MockAnalysisResult()

    class MockEvaluator:
        def __init__(self, repo, contract):
            pass

        def evaluate(self, res, rules):
            return archguard.cli.fitness_cmd._MOCK_RESULTS

    def mock_load_contract(repo):
        return archguard.cli.fitness_cmd._MOCK_CONTRACT

    def mock_parse_fitness(contract):
        return archguard.cli.fitness_cmd._MOCK_CONFIGS

    monkeypatch.setattr(
        "archguard.analysis.layers.AnalysisOrchestrator", MockOrchestrator
    )
    monkeypatch.setattr(
        "archguard.fitness.evaluator.FitnessFunctionEvaluator", MockEvaluator
    )
    monkeypatch.setattr("archguard.analysis.layers.load_contract", mock_load_contract)
    monkeypatch.setattr("archguard.config.parse_fitness_functions", mock_parse_fitness)


def set_mock_data(configs, results):
    archguard.cli.fitness_cmd._MOCK_CONTRACT = {}
    archguard.cli.fitness_cmd._MOCK_CONFIGS = configs
    archguard.cli.fitness_cmd._MOCK_RESULTS = results


def test_fitness_check_no_rules(mock_pipeline):
    set_mock_data([], [])
    result = runner.invoke(app, ["fitness", "check"])
    assert result.exit_code == 0
    assert "No fitness_functions defined" in strip_rich(result.stdout)


def test_fitness_check_all_pass(mock_pipeline):
    configs = [FitnessFunctionConfig(name="rule1", rule="r1", severity="critical")]
    results = [FitnessFunctionResult(rule="r1", passed=True)]
    set_mock_data(configs, results)

    result = runner.invoke(app, ["fitness", "check"])
    assert result.exit_code == 0
    assert "PASS" in strip_rich(result.stdout)
    assert "1/1 functions passed" in strip_rich(result.stdout)


def test_fitness_check_critical_fail(mock_pipeline):
    configs = [
        FitnessFunctionConfig(name="rule_crit", rule="r_crit", severity="critical")
    ]
    results = [
        FitnessFunctionResult(rule="r_crit", passed=False, details="Crit details")
    ]
    set_mock_data(configs, results)

    result = runner.invoke(app, ["fitness", "check"])
    assert result.exit_code == 1
    assert "FAIL" in strip_rich(result.stdout)
    assert "rule_crit" in strip_rich(result.stdout)
    assert "critical" in strip_rich(result.stdout)


def test_fitness_check_warn_fail_no_flag(mock_pipeline):
    configs = [FitnessFunctionConfig(name="rule_warn", rule="r_warn", severity="warn")]
    results = [
        FitnessFunctionResult(rule="r_warn", passed=False, details="Warn details")
    ]
    set_mock_data(configs, results)

    result = runner.invoke(app, ["fitness", "check"])
    assert result.exit_code == 0
    assert "FAIL" in strip_rich(result.stdout)
    assert "warn" in strip_rich(result.stdout)


def test_fitness_check_warn_fail_with_flag(mock_pipeline):
    configs = [FitnessFunctionConfig(name="rule_warn", rule="r_warn", severity="warn")]
    results = [
        FitnessFunctionResult(rule="r_warn", passed=False, details="Warn details")
    ]
    set_mock_data(configs, results)

    result = runner.invoke(app, ["fitness", "check", "--fail-on-warn"])
    assert result.exit_code == 2
    assert "FAIL" in strip_rich(result.stdout)


def test_fitness_check_json_output(mock_pipeline):
    configs = [
        FitnessFunctionConfig(
            name="rule_info", rule="r_info", severity="info", rationale="Info reason"
        )
    ]
    results = [FitnessFunctionResult(rule="r_info", passed=False, details="Info error")]
    set_mock_data(configs, results)

    result = runner.invoke(app, ["fitness", "check", "--json"])
    assert result.exit_code == 0

    # Must be valid JSON
    data = json.loads(strip_rich(result.stdout))
    assert len(data) == 1
    assert data[0]["name"] == "rule_info"
    assert data[0]["passed"] is False
    assert data[0]["severity"] == "info"
    assert data[0]["evidence"] == "Info error"
    assert data[0]["rationale"] == "Info reason"
