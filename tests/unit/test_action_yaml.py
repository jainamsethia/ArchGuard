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


def _entrypoint() -> str:
    return Path("action/entrypoint.sh").read_text(encoding="utf-8")


def test_entrypoint_consumes_every_declared_input() -> None:
    """Each declared input must be read by the entrypoint.

    Regression: entrypoint.sh was `exec archguard "$@"`. A docker action with
    no `args:` passes no arguments, so all eleven INPUT_* variables that
    action.yml advertises were silently ignored and the action was a no-op that
    exited 0. Declaring an input the entrypoint never reads is a lie in the
    action's public interface.
    """
    data = yaml.safe_load(Path("action/action.yml").read_text(encoding="utf-8"))
    script = _entrypoint()

    unread = [
        name
        for name in data["inputs"]
        if f"INPUT_{name.upper().replace('-', '_')}" not in script
    ]
    assert not unread, f"action.yml declares inputs entrypoint.sh never reads: {unread}"


def test_entrypoint_publishes_every_declared_output() -> None:
    data = yaml.safe_load(Path("action/action.yml").read_text(encoding="utf-8"))
    script = _entrypoint()

    assert "GITHUB_OUTPUT" in script, "entrypoint never writes step outputs"
    missing = [name for name in data["outputs"] if f"{name}=" not in script]
    assert not missing, f"action.yml declares outputs entrypoint.sh never sets: {missing}"
