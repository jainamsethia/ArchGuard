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


def _suppression_store_for(repo_url: str | None, job_id: str | None) -> Any:
    """Resolve the dashboard's suppression store.

    A module-level seam rather than an inline import: it keeps the dependency on
    the route layer explicit and lets tests substitute a store without needing a
    real one on disk.
    """
    from archguard.dashboard.routes.suppression import _suppression_store

    return _suppression_store(repo_url, job_id)


def select_findings(run: dict[str, Any], job_id: str | None) -> Selection:
    """Rank a run's findings and take the top N for an LLM remediation plan.

    Suppressions are read from the dashboard's own store -- the one the
    Suppressions tab writes to. The analysis-time filter in
    ``archguard.analysis._suppression_filter`` reads the *cloned workspace* root
    instead, which for a dashboard job is a fresh temp directory that never
    holds the user's suppressions, so relying on it alone would spend LLM budget
    explaining findings the user had already dismissed.
    """
    try:
        # The run records the repository it analysed, so no lookup is needed
        # here -- which is what keeps this function synchronous.
        store = _suppression_store_for(run.get("repo_url"), job_id)
    except Exception as exc:
        logger.warning("Suppression store unavailable (%s); ranking without it.", exc)
        store = None

    def _is_suppressed(v: dict[str, Any]) -> bool:
        if store is None:
            return False
        try:
            return bool(
                store.is_suppressed(
                    str(v.get("module") or ""),
                    int(v.get("layer") or 0),
                    str(v.get("message") or ""),
                )
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
