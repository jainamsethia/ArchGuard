"""Atomic YAML contract writer for ArchGuard."""

from __future__ import annotations

import math
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


def _infer_path(files: list[str]) -> str:
    """Find common directory prefix of all files in a community.

    Always returns a forward-slash path ending with ``/``.
    """
    if not files:
        return "./"

    # Normalize all paths to forward slashes
    normalized = [f.replace("\\", "/") for f in files]

    # Get parent directories
    parents = [str(PurePosixPath(f).parent) for f in normalized]

    if len(set(parents)) == 1:
        parent = parents[0]
        return (parent + "/") if parent != "." else "./"

    # Find common path prefix by comparing parts
    parts_list = [PurePosixPath(p).parts for p in parents]
    common_parts: list[str] = []
    for parts in zip(*parts_list):
        if len(set(parts)) == 1:
            common_parts.append(parts[0])
        else:
            break

    if common_parts:
        return "/".join(common_parts) + "/"

    # Scattered: use most common top-level directory
    top_dirs = [
        PurePosixPath(f).parts[0]
        for f in normalized
        if PurePosixPath(f).parts
    ]
    if top_dirs:
        return Counter(top_dirs).most_common(1)[0][0] + "/"

    return "./"


def _model_weights_version() -> str:
    """Compute model weights version string: ``{year}-Q{quarter}``."""
    now = datetime.now(timezone.utc)
    quarter = (now.month - 1) // 3 + 1
    return f"{now.year}-Q{quarter}"


def write_contract(
    output_path: Path,
    communities: dict[str, list[str]],
    fan_outs: dict[str, int],
    coherence_warnings: list[str],
) -> None:
    """Build a contract dict, validate it, and write atomically.

    Atomic write strategy:
      1. Write to a temp file in the same directory.
      2. ``os.replace(tmp, output_path)`` — atomic on POSIX.
    """
    from archguard.contract.validator import validate_contract

    now = datetime.now(timezone.utc)

    modules: list[dict[str, Any]] = []
    for name, files in communities.items():
        fan_out = fan_outs.get(name, 0)
        budget = max(3, math.ceil(fan_out * 1.5))
        path = _infer_path(files)

        modules.append({
            "name": name,
            "paths": [path],
            "fan_out_at_init": fan_out,
            "coupling_budget": budget,
            "semantic_drift_threshold": 0.25,
        })

    contract: dict[str, Any] = {
        "schema_version": "3.0",
        "model_weights_version": _model_weights_version(),
        "generated_at": now.isoformat(),
        "generated_by": "archguard init",
        "modules": modules,
        "fail_threshold": 0.75,
        "warn_threshold": 0.50,
    }

    # Validate before writing
    validate_contract(contract)

    # Atomic write
    dir_path = output_path.parent
    dir_path.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        suffix=".yml", dir=str(dir_path),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            yaml.dump(contract, tmp, default_flow_style=False, sort_keys=False)
        os.replace(tmp_name, str(output_path))
    except BaseException:
        # Clean up temp file on any error
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
