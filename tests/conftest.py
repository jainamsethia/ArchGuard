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


@pytest.fixture(autouse=True)
def restore_semantic_model_cache() -> Any:
    """Undo any test's mutation of the process-global embedding-model cache.

    ``semantic._GLOBAL_MODEL_CACHE`` lives for the whole process. Tests that
    mock ``SentenceTransformer`` write a MagicMock into it and only clear it
    *before* use, so the mock outlives the test; a later real-model test then
    embeds through the mock, gets nothing back, and fails for a reason that has
    nothing to do with the code under test. Snapshot and restore rather than
    clear, so a genuinely loaded model stays cached for the rest of the run.
    """
    from archguard.analysis.semantic import _GLOBAL_MODEL_CACHE

    snapshot = dict(_GLOBAL_MODEL_CACHE)
    yield
    _GLOBAL_MODEL_CACHE.clear()
    _GLOBAL_MODEL_CACHE.update(snapshot)

def strip_rich(text: str) -> str:
    """Strip Rich/ANSI escape sequences so test assertions work on plain text.

    Rich Console instances at module level (file-scope `_console = Console()`)
    are constructed before pytest fixtures run, so env-var monkeypatching
    can't disable their colour output.  This helper removes ANSI escape codes
    from captured output.
    """
    import re
    _ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
    return _ANSI_RE.sub('', text).replace('\x1b(B', '').replace('\x1b[m', '')
