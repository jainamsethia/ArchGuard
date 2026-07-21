"""Integration tests for CLI error handling and graceful degradation.

Covers:
- Missing repo path
- Missing config file
- Invalid YAML config
- Config with wrong schema
- Non-existent repo
"""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from archguard.cli.main import app

runner = CliRunner()


def _write_config(repo: Path, data: dict) -> Path:
    """Write a .archguard.yml to the repo directory."""
    config_path = repo / ".archguard.yml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False)
    return config_path


# ── Missing config ──────────────────────────────────────────────────────────


def test_analyze_missing_config_exits_config_error(tmp_path):
    """analyze on a dir with no .archguard.yml exits with code 2 (EXIT_CONFIG_ERROR)."""
    # Create an empty repo-like directory (no config file)
    repo = tmp_path / "noconfig"
    repo.mkdir(parents=True, exist_ok=True)
    # Create a .git stub so the repo is recognized
    (repo / ".git").mkdir(exist_ok=True)

    result = runner.invoke(app, ["analyze", "--repo", str(repo), "--dry-run"])
    assert result.exit_code == 2


# ── Invalid YAML ────────────────────────────────────────────────────────────


def test_analyze_invalid_yaml_exits_config_error(tmp_path):
    """analyze with unparseable .archguard.yml exits with code 2."""
    repo = tmp_path / "badyaml"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".git").mkdir(exist_ok=True)
    # Write invalid YAML
    (repo / ".archguard.yml").write_text(": : invalid yaml :\n")

    result = runner.invoke(app, ["analyze", "--repo", str(repo), "--dry-run"])
    assert result.exit_code == 2


# ── Invalid config schema ───────────────────────────────────────────────────


def test_analyze_invalid_schema_exits_config_error(tmp_path):
    """analyze with a config missing required fields exits with code 2."""
    repo = tmp_path / "badschema"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".git").mkdir(exist_ok=True)
    # Write a config with wrong structure (modules is not a list)
    _write_config(repo, {"version": "3.0", "modules": "not-a-list"})

    result = runner.invoke(app, ["analyze", "--repo", str(repo), "--dry-run"])
    assert result.exit_code == 2


# ── Version mismatch ────────────────────────────────────────────────────────


def test_analyze_wrong_version_exits_config_error(tmp_path):
    """analyze with unsupported config version exits with code 2."""
    repo = tmp_path / "badversion"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".git").mkdir(exist_ok=True)
    _write_config(repo, {
        "version": "2.0",
        "modules": [{"name": "core", "path": "src/"}],
    })

    result = runner.invoke(app, ["analyze", "--repo", str(repo), "--dry-run"])
    assert result.exit_code == 2


# ── Type mismatch ───────────────────────────────────────────────────────────


def test_analyze_path_not_directory_exits_error(tmp_path):
    """analyze with --repo pointing to a file exits with code 2."""
    repo = tmp_path / "afile"
    repo.write_text("this is a file, not a directory")

    result = runner.invoke(app, ["analyze", "--repo", str(repo), "--dry-run"])
    assert result.exit_code == 2
