"""Unit tests for archguard status CLI command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from typer.testing import CliRunner

from archguard.cli.main import app
from tests.conftest import strip_rich

runner: CliRunner = CliRunner()


def _write_config(tmp_path: Path, data: dict[str, Any]) -> None:
    """Write a .archguard.yml config file to the given directory."""
    config_path = tmp_path / ".archguard.yml"
    with config_path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False)


class TestStatusCommand:
    """Tests for 'archguard status'."""

    def test_status_with_valid_config(
        self, tmp_path: Path, minimal_contract: dict[str, Any]
    ) -> None:
        """archguard status with valid .archguard.yml exits 0 and shows module names."""
        _write_config(tmp_path, minimal_contract)

        result = runner.invoke(app, ["status", "--repo", str(tmp_path)])

        assert result.exit_code == 0
        assert "core" in strip_rich(result.output)

    def test_status_with_no_config(self, tmp_path: Path) -> None:
        """archguard status with no config exits 2 (EXIT_CONFIG_ERROR)."""
        result = runner.invoke(app, ["status", "--repo", str(tmp_path)])

        assert result.exit_code == 2

    def test_status_json_output(
        self, tmp_path: Path, minimal_contract: dict[str, Any]
    ) -> None:
        """archguard status --json exits 0 and output is valid JSON."""
        _write_config(tmp_path, minimal_contract)

        result = runner.invoke(app, ["status", "--repo", str(tmp_path), "--json"])

        assert result.exit_code == 0
        # Parse the JSON output — strip any ANSI/Rich markup whitespace
        output_text = strip_rich(result.output).strip()
        parsed: dict[str, Any] = json.loads(output_text)
        assert parsed["version"] == "3.0"
        assert parsed["module_count"] == 1
