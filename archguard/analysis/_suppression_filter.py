"""Suppression filtering for analysis violations."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger: logging.Logger = logging.getLogger(__name__)


def _filter_suppressed(
    repo_root: Path,
    violations: list[Any],
) -> list[Any]:
    """Remove violations that match an active suppression."""
    try:
        from archguard.suppression.store import SuppressionStore

        store = SuppressionStore(repo_root)
        return [
            v
            for v in violations
            if not store.is_suppressed(v.module, v.layer, v.message)
        ]
    except Exception as exc:
        logger.warning(
            "Suppression store unavailable (%s). Proceeding unfiltered.", exc
        )
        return violations
