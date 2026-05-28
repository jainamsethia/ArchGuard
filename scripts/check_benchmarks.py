#!/usr/bin/env python3
"""Check pytest-benchmark JSON output against latency thresholds.

Usage::

    python3 scripts/check_benchmarks.py benchmark-results.json

Exit 1 if any threshold exceeded.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

THRESHOLDS: dict[str, float] = {
    "test_analyze_warm_cache":       5.0,   # < 5s median  (AC-05)
    "test_import_parser_throughput": 1.0,   # < 1s for 500 imports
    "test_scoring_throughput":       0.1,   # < 100ms
    "test_validator_throughput":     0.05,  # < 50ms
}


def main() -> int:
    """Check benchmark results against defined thresholds."""
    if len(sys.argv) < 2:
        print("Usage: check_benchmarks.py <benchmark-results.json>")
        return 1

    results_path = Path(sys.argv[1])
    if not results_path.exists():
        print(f"File not found: {results_path}")
        return 1

    data: dict = json.loads(results_path.read_text(encoding="utf-8"))
    benchmarks: list[dict] = data.get("benchmarks", [])

    failures: list[str] = []
    for bench in benchmarks:
        name: str = bench["name"]
        median: float = bench["stats"]["median"]
        for pattern, threshold in THRESHOLDS.items():
            if pattern in name and median > threshold:
                failures.append(
                    f"FAIL  {name}: "
                    f"median={median:.3f}s > threshold={threshold}s"
                )

    if failures:
        print("Benchmark threshold violations:")
        for f in failures:
            print(f"  {f}")
        return 1

    print(f"All {len(benchmarks)} benchmarks passed thresholds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
