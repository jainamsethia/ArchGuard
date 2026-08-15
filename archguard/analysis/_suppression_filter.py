"""Suppression filtering for analysis violations."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger: logging.Logger = logging.getLogger(__name__)


def _filter_suppressed(
    repo_root: Path,
    violations: list[Any],
    store_path: Path | None = None,
) -> list[Any]:
    """Remove violations that match an active suppression.

    *store_path*, when given, overrides the default
    ``<repo_root>/.archguard-cache/suppressions.jsonl`` location. The default is
    correct for the CLI, where the analysed checkout persists between runs and
    the suppression file lives inside it. It is wrong for the dashboard, which
    analyses a throwaway clone: the file it would look for there is one no user
    ever wrote to, so every suppressed violation came back on the next scan.
    """
    try:
        from archguard.suppression.store import SuppressionStore

        store = (
            SuppressionStore.at_path(store_path)
            if store_path is not None
            else SuppressionStore(repo_root)
        )
        kept = [
            v
            for v in violations
            if not store.is_suppressed(v.module, v.layer, v.message)
        ]
        if len(kept) != len(violations):
            logger.info(
                "Suppressed %d of %d violations from %s",
                len(violations) - len(kept), len(violations), store._path,
            )
        return kept
    except Exception as exc:
        logger.warning(
            "Suppression store unavailable (%s). Proceeding unfiltered.", exc
        )
        return violations
