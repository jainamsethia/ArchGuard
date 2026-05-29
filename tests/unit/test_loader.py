"""Unit tests for archguard.contract.loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from archguard.contract.loader import load_contract
from archguard.utils.errors import ConfigError


class TestLoadContract:
    """Tests for load_contract()."""

    def test_single_file_loads_correctly(
        self, tmp_path: Path, minimal_contract: dict[str, Any]
    ) -> None:
        """Single .archguard.yml loads and validates successfully."""
        config_path = tmp_path / ".archguard.yml"
        with config_path.open("w", encoding="utf-8") as f:
            yaml.dump(minimal_contract, f, default_flow_style=False)

        result = load_contract(tmp_path)

        assert result["version"] == "3.0"
        assert len(result["modules"]) == 1
        assert result["modules"][0]["name"] == "core"

    def test_multi_file_merge(self, tmp_path: Path) -> None:
        """Multiple .archguard/*.yml files merge (modules concatenated)."""
        config_dir = tmp_path / ".archguard"
        config_dir.mkdir()

        file1_data: dict[str, Any] = {
            "version": "3.0",
            "modules": [{"name": "core", "path": "src/core/"}],
        }
        file2_data: dict[str, Any] = {
            "version": "3.0",
            "modules": [{"name": "api", "path": "src/api/"}],
        }

        with (config_dir / "01_core.yml").open("w", encoding="utf-8") as f:
            yaml.dump(file1_data, f, default_flow_style=False)
        with (config_dir / "02_api.yml").open("w", encoding="utf-8") as f:
            yaml.dump(file2_data, f, default_flow_style=False)

        result = load_contract(tmp_path)

        assert len(result["modules"]) == 2
        names = [m["name"] for m in result["modules"]]
        assert "core" in names
        assert "api" in names

    def test_no_config_raises_config_error(self, tmp_path: Path) -> None:
        """No config file raises ConfigError."""
        with pytest.raises(ConfigError, match="No ArchGuard configuration found"):
            load_contract(tmp_path)

    def test_invalid_yaml_raises_config_error(self, tmp_path: Path) -> None:
        """Invalid YAML raises ConfigError."""
        config_path = tmp_path / ".archguard.yml"
        config_path.write_text("{{{{invalid yaml: [", encoding="utf-8")

        with pytest.raises(ConfigError, match="Failed to parse YAML"):
            load_contract(tmp_path)
