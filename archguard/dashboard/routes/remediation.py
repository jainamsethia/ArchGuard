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


@app.post(
    "/api/remediation/plan",
    dependencies=[Depends(check_token), Depends(_llm_rate_limit)],
)
async def remediation_plan(body: RemediationRequest) -> Any:
    """Generate a remediation plan from the provided violations."""
    from archguard.llm.remediation import generate_remediation_plan

    try:
        result = await generate_remediation_plan(body.violations)
        return result
    except Exception as exc:
        logging.warning("Remediation endpoint error: %s", exc)
        return {"tasks": [], "error": str(exc)}


@app.get(
    "/api/remediation/plan",
    dependencies=[Depends(check_token), Depends(_llm_rate_limit)],
)
async def remediation_plan_from_audit(
    limit: int = Query(default=1, ge=1, le=10),
    job_id: str | None = None
) -> Any:
    """Generate a remediation plan from the latest audit run violations."""
    from archguard.llm.remediation import generate_remediation_plan

    audit = AuditLogger(get_audit_path(job_id))
    latest = audit.read_last_run() or {}
    violations = latest.get("violations", [])

    try:
        result = await generate_remediation_plan(violations)
        return result
    except Exception as exc:
        logging.warning("Remediation endpoint error: %s", exc)
        return {"tasks": [], "error": str(exc)}
