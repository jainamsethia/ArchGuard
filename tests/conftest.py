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
        "version": "3.0",
        "modules": [
            {
                "name": "core",
                "path": "src/core/",
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


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure GEMINI_API_KEY and OLLAMA_HOST are not set during tests unless explicitly patched."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
