"""Removing the findings a user has chosen to ignore."""

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
    """Drop violations whose identity the caller has marked as suppressed.

    *suppressed_hashes* is the set of ``make_violation_hash`` digests to hide,
    resolved by the caller. Passed as data rather than looked up here because
    this runs inside the analysis thread, which holds no database session --
    and because whose suppressions apply is a question about the person who
    submitted the job, not about the checkout on disk.

    It used to read a JSONL file named after the repository. That file was
    shared by every account that had analysed the same repository, so one
    user's suppression removed a finding from a stranger's report.

    ``None`` means nothing is suppressed, which is what a caller with no user
    context wants.
    """
    if not suppressed_hashes:
        return violations

    try:
        from archguard.suppression.models import make_violation_hash

        kept = [
            v
            for v in violations
            if make_violation_hash(v.module, v.layer, v.message) not in suppressed_hashes
        ]
        if len(kept) != len(violations):
            logger.info(
                "Suppressed %d of %d violations",
                len(violations) - len(kept),
                len(violations),
            )
        return kept
    except Exception as exc:
        # Reporting a finding that someone asked to hide is a smaller failure
        # than hiding one nobody did, so an error here shows everything.
        logger.warning("Could not apply suppressions (%s). Reporting unfiltered.", exc)
        return violations
