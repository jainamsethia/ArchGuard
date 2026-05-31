"""Benchmark configuration and fixtures."""

from __future__ import annotations

import platform
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register benchmark markers."""
    config.addinivalue_line("markers", "benchmark: latency benchmark test")


@pytest.fixture(autouse=True)
def require_x86_64() -> None:
    """All benchmarks must run on x86_64 per AC-04/AC-05."""
    machine = platform.machine()
    if machine not in ("x86_64", "AMD64"):
        pytest.skip(f"Benchmarks require x86_64, got {machine}")


@pytest.fixture(scope="session")
def fixture_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a minimal Python repo fixture for benchmarks.

    20 Python files across 3 modules, each ~50 lines, plus a contract.
    """
    base: Path = tmp_path_factory.mktemp("bench_repo")

    for module in ["payments", "orders", "core"]:
        mod_dir = base / "src" / module
        mod_dir.mkdir(parents=True)
        (mod_dir / "__init__.py").write_text("")
        for i in range(6):
            (mod_dir / f"service_{i}.py").write_text(
                f'"""Module {module} service {i}."""\n\n'
                + "\n".join(
                    line
                    for j in range(3)
                    for line in [
                        f"def function_{j}(x: int) -> int:",
                        f'    """Process {module} item."""',
                        "    result = x * 2",
                        "    return result",
                        "",
                    ]
                )
            )

    (base / ".archguard.yml").write_text(
        'version: "3.0"\n'
        "modules:\n"
        "  - name: payments\n    path: src/payments/\n"
        "  - name: orders\n    path: src/orders/\n"
        "  - name: core\n    path: src/core/\n"
        "skip_layers:\n  - semantic\n  - duplication\n"
        "fail_threshold: 0.75\nwarn_threshold: 0.50\n"
    )

    # Initialize git repo so git-diff based change detection works
    import subprocess
    subprocess.run(["git", "init"], cwd=base, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=base, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=base, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=base, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=base,
        capture_output=True,
    )

    return base
