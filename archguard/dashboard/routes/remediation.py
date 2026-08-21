"""Remediation-plan generation endpoints."""

import logging
from typing import Any

from fastapi import Depends, Query
from pydantic import BaseModel, Field

from archguard.dashboard._auth import check_token
from archguard.dashboard._identity import current_user
from archguard.dashboard._rate_limit import _llm_rate_limit
from archguard.dashboard.app import app
from archguard.db.models import User

logger = logging.getLogger(__name__)


class RemediationRequest(BaseModel):
    violations: list[dict[str, Any]] = Field(
        default=[],
        max_length=50,
        description="Violations to remediate (max 50 items)",
    )


def _selection_summary_no_job(selection: Any) -> dict[str, Any]:
    """Selection counts for the job-less POST variant (no suppression store)."""
    from archguard.dashboard._selection import selection_summary

    return selection_summary(selection)


def _mock_remediation_response(violations: Any) -> dict[str, Any]:
    print(f"--- MOCK LLM PROMPT ---\nRemediation for: {violations}\n--- END MOCK LLM PROMPT ---")
    return {
        "tasks": [
            {
                "title": "Mock Remediation Task",
                "description": "Mock description for testing",
                "priority": "low",
                "effort_days": 1,
                "acceptance_criteria": ["Mock criteria"],
            }
        ]
    }

@app.post(
    "/api/v1/remediation/plan",
    dependencies=[Depends(check_token), Depends(_llm_rate_limit)],
)
@app.post(
    "/api/remediation/plan",
    dependencies=[Depends(check_token), Depends(_llm_rate_limit)],
    deprecated=True,
)
async def remediation_plan(body: RemediationRequest) -> Any:
    """Generate a remediation plan from the provided violations."""
    import os
    if os.environ.get("ARCHGUARD_MOCK_LLM") == "1":
        return _mock_remediation_response(body.violations)

    from archguard.analysis.ranking import select_for_remediation
    from archguard.llm.remediation import RemediationUnavailableError, generate_remediation_plan

    # Rank and cap even when the caller supplies the list. Without this the LLM
    # receives whatever order the caller happened to send, and a LOW-severity
    # finding can crowd out a CRITICAL one. No suppression store is consulted
    # here -- this endpoint has no job context; the GET variant below does.
    selection = select_for_remediation(body.violations)

    try:
        result = await generate_remediation_plan(
            [r.finding for r in selection.selected]
        )
        result["selection"] = _selection_summary_no_job(selection)
        return result
    except RemediationUnavailableError as exc:
        return {"tasks": [], "error": str(exc)}
    except Exception as exc:
        logger.warning("Remediation endpoint error: %s", exc)
        return {"tasks": [], "error": "An unexpected error occurred generating the remediation plan."}


@app.get(
    "/api/v1/remediation/plan",
    dependencies=[Depends(check_token), Depends(_llm_rate_limit)],
)
@app.get(
    "/api/remediation/plan",
    dependencies=[Depends(check_token), Depends(_llm_rate_limit)],
    deprecated=True,
)
async def remediation_plan_from_audit(
    limit: int = Query(default=1, ge=1, le=10),
    job_id: str | None = None,
    user: User = Depends(current_user),
) -> Any:
    """Generate a remediation plan from the latest audit run violations."""
    import os
    if os.environ.get("ARCHGUARD_MOCK_LLM") == "1":
        return _mock_remediation_response(f"Audit job_id={job_id}")

    from archguard.llm.remediation import RemediationUnavailableError, generate_remediation_plan

    if job_id:
        from archguard.db import store
        from archguard.db.session import session_scope

        async with session_scope() as session:
            latest = await store.get_latest_run(session, job_id, user.id)
        if latest is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"No run found for job_id {job_id}")
    else:
        return {"empty": True, "message": "No analysis selected. Submit or select a repository to see health data."}
    from archguard.dashboard._selection import select_findings, selection_summary

    selection = select_findings(latest, job_id)
    violations = [r.finding for r in selection.selected if not r.is_fitness_gate]
    gates = [r.finding for r in selection.selected if r.is_fitness_gate]

    try:
        result = await generate_remediation_plan(violations, fitness_failures=gates)
        result["selection"] = selection_summary(selection)
        return result
    except RemediationUnavailableError as exc:
        return {"tasks": [], "error": str(exc)}
    except Exception as exc:
        logger.warning("Remediation endpoint error: %s", exc)
        return {"tasks": [], "error": "An unexpected error occurred generating the remediation plan."}
