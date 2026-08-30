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


def select_findings(
    run: dict[str, Any], suppressed_hashes: set[str] | None = None
) -> Selection:
    """Rank a run's findings and take the top N for an LLM remediation plan.

    *suppressed_hashes* is what the account that owns this run has chosen to
    hide, resolved by the caller. Findings matching one are excluded, so LLM
    budget is not spent explaining something the user already dismissed.

    Passed in rather than looked up. This function is synchronous and the
    answer lives in PostgreSQL; more importantly, the suppressions that apply
    belong to a person, and they used to be read from a file named after the
    repository -- shared by every account that had analysed it.
    """
    hidden = suppressed_hashes or set()

    def _is_suppressed(v: dict[str, Any]) -> bool:
        if not hidden:
            return False
        try:
            from archguard.suppression.models import make_violation_hash

            return make_violation_hash(
                str(v.get("module") or ""),
                int(v.get("layer") or 0),
                str(v.get("message") or ""),
            ) in hidden
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
