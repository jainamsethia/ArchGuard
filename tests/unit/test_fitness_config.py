"""Tests for FitnessFunctionConfig and parse_fitness_functions."""

from pathlib import Path

import pytest
import yaml

from archguard.config import (
    FitnessFunctionConfig,
    FitnessFunctionConfigError,
    parse_fitness_functions,
)
from archguard.contract.loader import load_contract

# ---------------------------------------------------------------------------
# Step D tests (from execution plan)
# ---------------------------------------------------------------------------


def test_valid_fitness_function_config():
    """A well-formed config should construct without errors."""
    cfg = FitnessFunctionConfig(
        name="no_cycles",
        rule="graph.cycles == 0",
        severity="critical",
        rationale="Cycles break modularity.",
    )
    assert cfg.name == "no_cycles"
    assert cfg.rule == "graph.cycles == 0"
    assert cfg.severity == "critical"
    assert cfg.rationale == "Cycles break modularity."


def test_invalid_severity():
    """severity='blocker' is not in {critical, warn, info} and must raise."""
    with pytest.raises(FitnessFunctionConfigError, match="severity must be one of"):
        FitnessFunctionConfig(
            name="bad_sev",
            rule="health_score >= 50",
            severity="blocker",
        )


def test_empty_rule():
    """An empty rule string must raise."""
    with pytest.raises(FitnessFunctionConfigError, match="rule must be non-empty"):
        FitnessFunctionConfig(
            name="empty_rule",
            rule="",
            severity="warn",
        )


def test_whitespace_only_rule():
    """A whitespace-only rule must also raise."""
    with pytest.raises(FitnessFunctionConfigError, match="rule must be non-empty"):
        FitnessFunctionConfig(
            name="blank_rule",
            rule="   ",
            severity="warn",
        )


def test_missing_fitness_functions_section():
    """A contract dict with no fitness_functions key should yield an empty list."""
    contract = {"version": "3.0", "modules": [{"name": "a", "path": "a/"}]}
    result = parse_fitness_functions(contract)
    assert result == []


def test_full_config_with_fitness():
    """parse_fitness_functions should produce 8 configs from the root .archguard.yml."""
    root = Path(__file__).resolve().parents[2]  # project root
    config_path = root / ".archguard.yml"
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    configs = parse_fitness_functions(data)
    assert len(configs) == 8

    names = [c.name for c in configs]
    assert "no_circular_dependencies" in names
    assert "archguard_cannot_import_tests" in names
    assert "health_gate" in names
    assert "violation_sanity" in names


def test_root_archguard_yml_loads_successfully():
    """The root .archguard.yml must pass JSON Schema validation via load_contract."""
    root = Path(__file__).resolve().parents[2]
    contract = load_contract(root)
    assert contract["version"] == "3.0"
    assert "fitness_functions" in contract
    assert len(contract["fitness_functions"]) == 8


def test_fixture_archguard_yml_loads_successfully():
    """The fixture .archguard.yml (no fitness_functions) must still load."""
    fixture_root = (
        Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "sample_repo"
    )
    contract = load_contract(fixture_root)
    assert contract["version"] == "3.0"
    # No fitness_functions section — parse should return empty
    configs = parse_fitness_functions(contract)
    assert configs == []


def test_default_severity():
    """When severity is omitted, it should default to 'warn'."""
    cfg = FitnessFunctionConfig(name="default_sev", rule="health_score >= 50")
    assert cfg.severity == "warn"


def test_default_rationale():
    """When rationale is omitted, it should default to empty string."""
    cfg = FitnessFunctionConfig(name="no_rationale", rule="graph.cycles == 0")
    assert cfg.rationale == ""
