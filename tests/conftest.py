"""Shared fixtures for ArchGuard tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture()
def minimal_contract() -> dict[str, Any]:
    """Return a minimal valid contract dict."""
    return {
        "schema_version": "3.0",
        "modules": [
            {
                "name": "core",
                "paths": ["src/core/"],
            }
        ],
    }


@pytest.fixture()
def write_config(tmp_path: Path) -> Any:
    """Factory fixture: write a YAML config to tmp_path/.archguard.yml."""

    def _write(data: dict[str, Any], filename: str = ".archguard.yml") -> Path:
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False)
        return path

    return _write
