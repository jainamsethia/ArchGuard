"""Unit tests for action/action.yml validation."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_action_yml_is_valid_yaml() -> None:
    content = Path("action/action.yml").read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    assert data["name"] == "ArchGuard"
    assert "inputs" in data
    assert "outputs" in data
    assert data["runs"]["using"] == "docker"


def test_action_yml_has_required_inputs() -> None:
    data = yaml.safe_load(
        Path("action/action.yml").read_text(encoding="utf-8"),
    )
    inputs = data["inputs"]
    assert "repo-root" in inputs
    assert "pr-number" in inputs
    assert "skip-explanation" in inputs
    assert "fail-on-warn" in inputs
    assert "dry-run" in inputs


def test_action_yml_has_outputs() -> None:
    data = yaml.safe_load(
        Path("action/action.yml").read_text(encoding="utf-8"),
    )
    outputs = data["outputs"]
    assert "archdebt-score" in outputs
    assert "archdebt-band" in outputs


def test_action_yml_branding() -> None:
    data = yaml.safe_load(
        Path("action/action.yml").read_text(encoding="utf-8"),
    )
    assert data["branding"]["icon"] == "shield"
    assert data["branding"]["color"] == "blue"
