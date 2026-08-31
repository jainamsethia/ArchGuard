"""Shared fixtures for ArchGuard tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

# Re-exported so every test can ask for a real database without importing a
# path-relative module. See tests/db_fixtures.py for why nothing here is faked.
from tests.db_fixtures import (  # noqa: F401
    _clean_redis,
    _schema_at_head,
    _services_reachable,
    auth_client,
    live_db,
    requires_postgres,
    seed_run,
    test_user,
)


@pytest.fixture()
def resolves_only(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Answer one hostname with one address, and leave every other name alone.

    The SSRF guard resolves a webhook's hostname before accepting it, so any
    test that configures a webhook and expects it to be *accepted* performs a
    live DNS lookup for whatever name it used. That makes the test's result
    depend on the resolver being reachable and on what it answers today, which
    is how `test_a_watch_never_returns_the_webhook_url` came to fail once in a
    run and pass on retry.

    Deliberately narrow rather than a blanket stub. `socket.getaddrinfo` is how
    asyncpg reaches PostgreSQL too, so replacing it wholesale sends the database
    connection wherever the webhook test happened to point -- which surfaces
    several frames away as a foreign key violation with nothing to do with
    webhooks. Every name except the one named here goes to the real resolver.

    Returns the list of names that were looked up, so a test can assert its
    hostname went through the stub rather than out to the network.
    """
    import socket

    real = socket.getaddrinfo
    lookups: list[str] = []

    def _install(hostname: str, address: str) -> list[str]:
        def _resolver(host: Any, port: Any = None, *args: Any, **kwargs: Any) -> Any:
            # anyio IDNA-encodes names before resolving, so the same lookup can
            # arrive as bytes or as str depending on the caller.
            name = (
                host.decode("ascii") if isinstance(host, bytes | bytearray) else str(host)
            )
            if name != hostname:
                return real(host, port, *args, **kwargs)
            lookups.append(name)
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            sockaddr = (
                (address, port or 443, 0, 0)
                if family == socket.AF_INET6
                else (address, port or 443)
            )
            return [(family, socket.SOCK_STREAM, 6, "", sockaddr)]

        monkeypatch.setattr(socket, "getaddrinfo", _resolver)
        return lookups

    return _install


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
def unpooled_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test against an unpooled engine.

    TestClient opens a fresh event loop per request when it is not used as a
    context manager, and an asyncpg connection is bound to the loop that opened
    it -- so a pooled connection handed to the next request raises "Event loop
    is closed" rather than reconnecting. NullPool sidesteps it entirely, at a
    cost (one connect per query) that only matters under load.
    """
    monkeypatch.setenv("ARCHGUARD_DB_POOL_SIZE", "0")


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
