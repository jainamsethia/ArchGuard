"""Remediation-plan generation endpoints."""

import logging
from typing import Any
from pydantic import BaseModel, Field
from fastapi import Depends, Query
from archguard.dashboard.app import app, get_audit_path
from archguard.dashboard._auth import check_token
from archguard.dashboard._rate_limit import _llm_rate_limit
from archguard.audit.logger import AuditLogger


class RemediationRequest(BaseModel):
    violations: list[dict[str, Any]] = Field(
        default=[],
        max_length=50,
        description="Violations to remediate (max 50 items)",
    )


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

    from archguard.llm.remediation import generate_remediation_plan

    try:
        result = await generate_remediation_plan(body.violations)
        return result
    except Exception as exc:
        logging.warning("Remediation endpoint error: %s", exc)
        return {"tasks": [], "error": str(exc)}


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
    job_id: str | None = None
) -> Any:
    """Generate a remediation plan from the latest audit run violations."""
    import os
    if os.environ.get("ARCHGUARD_MOCK_LLM") == "1":
        return _mock_remediation_response(f"Audit job_id={job_id}")

    from archguard.llm.remediation import generate_remediation_plan

    audit = AuditLogger(get_audit_path(job_id))
    if job_id:
        runs = audit.read_last_n_runs(n=100)
        latest = next((r for r in runs if r.get("job_id") == job_id), None)
        if latest is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"No run found for job_id {job_id}")
    else:
        return {"empty": True, "message": "No analysis selected. Submit or select a repository to see health data."}
    violations = latest.get("violations", [])

    try:
        result = await generate_remediation_plan(violations)
        return result
    except Exception as exc:
        logging.warning("Remediation endpoint error: %s", exc)
        return {"tasks": [], "error": str(exc)}
