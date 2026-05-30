#!/usr/bin/env python3
"""Check pytest-benchmark JSON output against thresholds or compare current run against a baseline.

Usage::

    # Threshold mode:
    python3 scripts/check_benchmarks.py benchmark-results.json

    # Comparison mode:
    python3 scripts/check_benchmarks.py --baseline .benchmarks/baseline.json --current .benchmarks/current.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

THRESHOLDS: dict[str, float] = {
    "test_analyze_warm_cache": 5.0,  # < 5s median  (AC-05)
    "test_import_parser_throughput": 1.0,  # < 1s for 500 imports
    "test_scoring_throughput": 0.1,  # < 100ms
    "test_validator_throughput": 0.05,  # < 50ms
}


def check_thresholds(results_path: Path) -> int:
    """Check benchmark results against absolute thresholds."""
    if not results_path.exists():
        print(f"File not found: {results_path}")
        return 1

    data: dict[str, Any] = json.loads(results_path.read_text(encoding="utf-8"))
    benchmarks: list[dict[str, Any]] = data.get("benchmarks", [])

    failures: list[str] = []
    for bench in benchmarks:
        name: str = bench["name"]
        median: float = bench["stats"]["median"]
        for pattern, threshold in THRESHOLDS.items():
            if pattern in name and median > threshold:
                failures.append(
                    f"FAIL  {name}: median={median:.3f}s > threshold={threshold}s"
                )

    if failures:
        print("Benchmark threshold violations:")
        for f in failures:
            print(f"  {f}")
        return 1

    print(f"All {len(benchmarks)} benchmarks passed thresholds.")
    return 0


def compare_benchmarks(baseline_path: Path, current_path: Path) -> int:
    """Compare current benchmark results against baseline and fail if regression > 20%."""
    if not baseline_path.exists():
        print(f"Baseline file not found: {baseline_path}")
        return 1
    if not current_path.exists():
        print(f"Current file not found: {current_path}")
        return 1

    baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    current_data = json.loads(current_path.read_text(encoding="utf-8"))

    baseline_means = {
        bench["name"]: bench["stats"]["mean"]
        for bench in baseline_data.get("benchmarks", [])
    }

    failures = []
    for bench in current_data.get("benchmarks", []):
        name = bench["name"]
        current_mean = bench["stats"]["mean"]
        if name in baseline_means:
            baseline_mean = baseline_means[name]
            if baseline_mean > 0:
                ratio = current_mean / baseline_mean
                if ratio > 1.20:
                    failures.append(
                        f"REGRESSION: {name} mean execution time "
                        f"({current_mean:.6f}s) is {((ratio - 1) * 100):.1f}% slower "
                        f"than baseline ({baseline_mean:.6f}s)"
                    )

    if failures:
        print("Benchmark regression violations (>20% slowdown):")
        for f in failures:
            print(f"  {f}")
        return 1

    print("All benchmarks passed regression comparison (<20% slowdown).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check benchmark outputs.")
    parser.add_argument(
        "results_file",
        nargs="?",
        type=Path,
        help="Single benchmark results JSON file to check against absolute thresholds.",
    )
    parser.add_argument("--baseline", type=Path, help="Baseline JSON file.")
    parser.add_argument(
        "--current", type=Path, help="Current JSON file to compare against baseline."
    )

    args = parser.parse_args()

    if args.baseline and args.current:
        return compare_benchmarks(args.baseline, args.current)
    elif args.results_file:
        return check_thresholds(args.results_file)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
