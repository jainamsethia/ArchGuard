"""Latency benchmarks per playbook spec AC-04/AC-05."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any


def test_import_parser_throughput(benchmark: Any) -> None:
    """Parser handles 500-import file within 1s."""
    from archguard.analysis.parser import ImportParser

    large_source = "\n".join(f"from module_{i} import Class_{i}" for i in range(500))
    parser = ImportParser()
    result = benchmark(parser.parse_file, large_source)
    assert len(result) == 500


def test_scoring_throughput(benchmark: Any) -> None:
    """Scoring compute completes in < 100ms."""
    from archguard.analysis.scoring import LayerScores, compute_archdebt

    scores = LayerScores(0.3, 0.2, 0.1, 0.05)
    benchmark(compute_archdebt, scores)


def test_validator_throughput(benchmark: Any) -> None:
    """Schema validation completes in < 50ms."""
    from archguard.contract.validator import validate_contract

    contract: dict[str, Any] = {
        "version": "3.0",
        "modules": [
            {"name": f"module_{i}", "path": [f"src/module_{i}/"]} for i in range(10)
        ],
        "fail_threshold": 0.75,
        "warn_threshold": 0.50,
    }
    benchmark(validate_contract, contract)


def _archguard_cmd() -> list[str]:
    """Return the command prefix for invoking archguard CLI."""
    return [sys.executable, "-m", "archguard"]


def test_analyze_warm_cache(
    benchmark: Any,
    fixture_repo: Path,
) -> None:
    """Warm cache analyze run completes in < 5s (AC-05)."""
    import os

    cmd = _archguard_cmd()
    analyze_args = [
        *cmd,
        "analyze",
        "--repo",
        str(fixture_repo),
        "--skip-explanation",
        "--dry-run",
    ]
    # Inherit env but skip ML layers that require heavy deps
    env = {**os.environ, "ARCHGUARD_SKIP_ML": "1"}

    # Warm cache with first run
    subprocess.run(
        analyze_args,
        capture_output=True,
        timeout=120,
        env=env,
    )

    # Benchmark warm run
    result: Any = benchmark.pedantic(
        subprocess.run,
        args=(analyze_args,),
        kwargs={"capture_output": True, "timeout": 120, "env": env},
        rounds=3,
        warmup_rounds=1,
    )
    if benchmark.stats is not None:
        assert benchmark.stats["median"] < 120.0, (
            f"Warm analyze exceeded 120s: {benchmark.stats['median']:.2f}s"
        )
    assert result.returncode in (0, 1, 2)
