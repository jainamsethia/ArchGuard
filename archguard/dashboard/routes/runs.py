"""Run-history, module-list, trend, and dependency-graph read endpoints.

Every read here used to tail-scan ``.archguard-cache/audit.jsonl`` and filter in
Python -- up to 10,000 lines per request in the module endpoint. They are
queries now.

One consequence is worth naming: ``/repos/{url}/runs`` could only ever be
answered by scanning the whole file and string-matching ``repo_url``, and the
file truncated itself at 10 MB, so a repository's history quietly disappeared.
It is an indexed lookup on ``repositories.url`` now, which is what makes the
trend chart show something other than "not enough scan history yet".
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import Path as FastAPIPath

from archguard.dashboard._auth import check_token
from archguard.dashboard._identity import current_user
from archguard.dashboard._rate_limit import rate_limiter
from archguard.dashboard._workspace_paths import JobIdQuery
from archguard.db import store
from archguard.db.models import User
from archguard.db.session import session_scope

#: Mounted at /api/v1 by app.py. The dependencies live on the router rather
#: than on each decorator: repeating them per route is how one of them ends up
#: missing, and every route below this line reads user data.
router = APIRouter(dependencies=[Depends(check_token), Depends(rate_limiter)])


_logger = logging.getLogger(__name__)


@router.get("/repos/{repo_url:path}/runs")
async def get_repo_runs(
    repo_url: str,
    limit: int = Query(default=50, ge=1, le=500),
    user: User = Depends(current_user),
) -> Any:
    """This user's runs for one repository, across all their jobs that analysed it.

    Scoped to the caller: two people analysing the same public repository each
    see their own history, not a merged one. The URL is not a secret, so an
    unscoped version of this endpoint let anyone read anyone's results simply by
    guessing a popular repository.
    """
    async with session_scope() as session:
        runs = await store.get_runs_for_repository(
            session, repo_url, user.id, limit=limit
        )
    return {"repo_url": repo_url, "runs": runs, "total": len(runs)}


@router.get("/runs")
async def get_runs(
    limit: int = Query(default=50, ge=1, le=500),
    module: str | None = None,
    job_id: JobIdQuery = None,
    user: User = Depends(current_user),
) -> Any:
    async with session_scope() as session:
        if job_id:
            runs = await store.get_runs_for_job(session, job_id, user.id, limit=limit)
        else:
            runs = await store.get_recent_runs(session, user.id, limit=limit)
    if module:
        runs = [r for r in runs if module in (r.get("modules_analyzed") or [])]
    return {"runs": runs[:limit], "total": len(runs[:limit])}


async def _with_plain_language(
    run: dict[str, Any], user_id: int | None = None
) -> dict[str, Any]:
    """Attach plain-language explanations and the remediation-selection counts.

    The explanations are rendered here rather than stored, so runs persisted
    before the templates existed get one too, and so the wording has a single
    source of truth in ``archguard.analysis.plain_language`` instead of a copy
    in the frontend. Rendering is pure: static text plus the violation's own
    recorded numbers, with a generic fallback for any kind lacking a template.

    ``remediation_selection`` is computed with the same ranking the remediation
    endpoint uses, so the counts the UI shows describe the set the LLM would
    actually receive -- and are available without spending an LLM call.
    """
    from archguard.analysis.plain_language import explain_dict

    violations = run.get("violations")
    if not isinstance(violations, list):
        return run

    enriched = [
        {**v, "plain": explain_dict(v)} if isinstance(v, dict) else v
        for v in violations
    ]

    out = {**run, "violations": enriched}
    try:
        from archguard.dashboard._selection import (
            select_findings,
            selection_summary,
            suppressed_hashes_for,
        )

        hashes = (
            await suppressed_hashes_for(run.get("repo_url"), user_id)
            if user_id is not None
            else set()
        )
        out["remediation_selection"] = selection_summary(select_findings(run, hashes))
    except Exception as exc:
        _logger.warning("Could not compute remediation selection: %s", exc)
    return out


@router.get("/runs/latest")
async def get_latest_run(
    job_id: JobIdQuery = None, user: User = Depends(current_user)
) -> Any:
    if not job_id:
        return {
            "empty": True,
            "message": "No analysis selected. Submit or select a repository to see health data.",
        }
    async with session_scope() as session:
        run = await store.get_latest_run(session, job_id, user.id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No run found for job_id {job_id}")
    return await _with_plain_language(run, user.id)


@router.get("/modules")
async def get_modules(
    job_id: JobIdQuery = None, user: User = Depends(current_user)
) -> Any:
    """Module scores and the import graph for one analysis.

    Without a job_id there is no repository context, and answering from
    whatever this server analysed most recently would report a stranger's
    modules as the visitor's.
    """
    if not job_id:
        return {
            "empty": True,
            "modules": {},
            "edges": [],
            "message": "No analysis selected. Submit or select a repository to see module data.",
        }

    async with session_scope() as session:
        run = await store.get_latest_run(session, job_id, user.id)

    if run is None:
        return {"empty": True, "modules": {}, "edges": [], "message": "No run found."}

    return {
        "modules": run.get("module_scores") or {},
        # Persisted with the run, so the graph survives the workspace being
        # swept -- it used to be recomputed from the clone, which meant the
        # Dependencies tab went blank the moment the workspace expired.
        "edges": run.get("import_edges") or [],
    }


@router.get("/trends/{module}")
async def get_module_trends(
    module: str = FastAPIPath(
        ..., min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.]+$"
    ),
    limit: int = Query(default=30, ge=1, le=500),
    job_id: JobIdQuery = None,
    user: User = Depends(current_user),
) -> Any:
    async with session_scope() as session:
        if job_id:
            runs = await store.get_runs_for_job(session, job_id, user.id, limit=limit)
        else:
            runs = await store.get_recent_runs(session, user.id, limit=limit)
    trend = [
        {"timestamp": r["timestamp"], "score": (r.get("module_scores") or {}).get(module)}
        for r in runs
        if module in (r.get("module_scores") or {})
    ]
    return {"module": module, "trend": trend}


@router.get("/deps")
async def get_deps(
    job_id: JobIdQuery = None, user: User = Depends(current_user)
) -> Any:
    """Dependency vulnerability scan for one analysis.

    Stored separately from the run: the scan is triggered on demand, after the
    analysis, and folding it into run history is what previously injected a
    bogus entry into the trend chart.
    """
    from archguard.analysis.deps import analyze_dependencies
    from archguard.dashboard._workspace_paths import get_target_path

    if not job_id:
        raise HTTPException(
            status_code=400, detail="No analysis selected. Submit a job first."
        )

    async with session_scope() as session:
        persisted = await store.get_dependency_scan(session, job_id, user.id)
        # An unknown job and someone else's job are the same answer here. Both
        # mean "nothing of yours to show", and telling them apart is what makes
        # a job id worth guessing.
        owns_job = await store.get_job(session, job_id, user.id) is not None
    if persisted is not None:
        return persisted
    if not owns_job:
        raise HTTPException(
            status_code=404, detail=f"No analysis found for job_id {job_id}"
        )

    # No stored scan: it can only be produced from the clone, so an expired
    # workspace is an honest 410. A job we have no record of must not look like
    # a job that scanned clean.
    try:
        target = get_target_path(job_id)
    except HTTPException:
        raise HTTPException(
            status_code=410,
            detail="Analysis workspace expired. Re-run the analysis to scan dependencies.",
        )

    try:
        result = analyze_dependencies(target)
        output = {
            "score": result.score,
            "vulnerable_packages": [
                {
                    "package": v.package,
                    "version": v.version,
                    "id": v.vulnerability_id,
                    "description": v.description,
                }
                for v in result.vulnerabilities
            ],
            "scanned_packages": result.scanned_packages,
            "skipped": result.skipped,
            "skip_reason": result.skip_reason,
            "error": result.error,
        }
        try:
            async with session_scope() as session:
                await store.save_dependency_scan(session, job_id, user.id, output)
        except Exception as exc:
            # The scan succeeded; only caching it did not. Returning the
            # degraded payload here would report a healthy repository as
            # unscannable because of a database hiccup.
            _logger.warning("Could not store the dependency scan for %s: %s", job_id, exc)
        return output
    except Exception as e:
        _logger.warning("Dependency analysis failed for job_id=%s: %s", job_id, e)
        return {
            "score": 0.0,
            "vulnerable_packages": [],
            "scanned_packages": 0,
            "skipped": True,
            "skip_reason": "Dependency scan could not complete",
            "error": None,
        }
