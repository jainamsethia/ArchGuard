"""Shared ranking/suppression logic for a persisted run.

Used by two routes that must agree: ``/api/v1/runs/latest`` reports the counts
the UI displays, and ``/api/v1/remediation/plan`` decides which findings are
actually sent to the LLM. If they used separate logic the UI could promise
remediation for a set the LLM never received.
"""

from __future__ import annotations

import logging
from typing import Any

from archguard.analysis.ranking import Selection, finding_key, select_for_remediation

logger = logging.getLogger(__name__)


async def suppressed_hashes_for(repo_url: str | None, user_id: int) -> set[str]:
    """This user's active suppression hashes for *repo_url*.

    Suppressions live in PostgreSQL, so reading them is async, while ranking is
    not. Resolving them to a plain set here lets the caller -- an async route --
    do the query, and keeps ``select_findings`` synchronous.

    Failure is empty rather than fatal: ranking without suppressions shows a
    user findings they had dismissed, which is a worse report but a report.
    """
    if not repo_url:
        return set()
    try:
        from archguard.db.session import session_scope
        from archguard.db.store import active_violation_hashes

        async with session_scope() as session:
            return await active_violation_hashes(session, repo_url, user_id)
    except Exception as exc:
        logger.warning("Suppressions unavailable (%s); ranking without them.", exc)
        return set()


def select_findings(
    run: dict[str, Any], suppressed_hashes: set[str] | None = None
) -> Selection:
    """Rank a run's findings and take the top N for an LLM remediation plan.

    *suppressed_hashes* comes from ``suppressed_hashes_for`` above -- the
    suppressions the user recorded in the Suppressions tab. The analysis-time
    filter cannot stand in for them on its own: for a dashboard job it runs
    against a throwaway clone, so relying on it would spend LLM budget
    explaining findings the user had already dismissed.
    """
    from archguard.suppression.models import make_violation_hash

    hashes = suppressed_hashes or set()

    def _is_suppressed(v: dict[str, Any]) -> bool:
        if not hashes:
            return False
        try:
            return (
                make_violation_hash(
                    str(v.get("module") or ""),
                    int(v.get("layer") or 0),
                    str(v.get("message") or ""),
                )
                in hashes
            )
        except (ValueError, TypeError):
            return False

    return select_for_remediation(
        run.get("violations", []),
        fitness_results=(run.get("metrics") or {}).get("fitness_results", []),
        is_suppressed=_is_suppressed,
    )


def selection_summary(selection: Selection) -> dict[str, Any]:
    """Counts plus the keys of the findings that would get a plan.

    ``selected`` is everything sent to the LLM, which includes any failed
    critical gate. ``selected_violations`` counts only table rows, and is what
    the UI quotes -- a gate is not a row, so quoting ``selected`` there would
    promise one more marked row than the table can ever show.
    """
    keys = [
        finding_key(r.finding) for r in selection.selected if not r.is_fitness_gate
    ]
    return {
        "detected": selection.detected_count,
        "suppressed": selection.suppressed_count,
        "eligible": selection.eligible_count,
        "limit": selection.limit,
        "selected": selection.selected_count,
        "selected_violations": len(keys),
        "selected_keys": keys,
    }
