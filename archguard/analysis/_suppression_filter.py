"""Suppression filtering for analysis violations."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger: logging.Logger = logging.getLogger(__name__)


def _filter_suppressed(
    repo_root: Path,
    violations: list[Any],
    suppressed_hashes: set[str] | None = None,
) -> list[Any]:
    """Remove violations the user has suppressed.

    *suppressed_hashes* is resolved by the caller, because suppressions live in
    PostgreSQL and this runs inside a synchronous pipeline. The worker reads
    them once per job -- scoped to the user who submitted it -- and passes the
    set down; ``None`` or an empty set means filter nothing.

    This used to open a JSONL file under *repo_root* instead, which for a
    dashboard job is a throwaway clone that has never held anybody's
    suppressions. *repo_root* is kept in the signature because the orchestrator
    passes it positionally and it costs nothing to accept, but nothing here
    reads from disk any more.
    """
    if not suppressed_hashes:
        return violations

    try:
        from archguard.suppression.models import make_violation_hash

        kept = [
            v
            for v in violations
            if make_violation_hash(v.module, v.layer, v.message)
            not in suppressed_hashes
        ]
        if len(kept) != len(violations):
            logger.info(
                "Suppressed %d of %d violations",
                len(violations) - len(kept),
                len(violations),
            )
        return kept
    except Exception as exc:
        logger.warning(
            "Could not apply suppressions (%s). Proceeding unfiltered.", exc
        )
        return violations
