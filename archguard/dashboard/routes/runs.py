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

from fastapi import Depends, HTTPException, Query
from fastapi import Path as FastAPIPath

from archguard.dashboard._auth import check_token
from archguard.dashboard._rate_limit import rate_limiter
from archguard.dashboard.app import JobIdQuery, app
from archguard.db import store
from archguard.db.session import session_scope

_logger = logging.getLogger(__name__)


@app.get(
    "/api/v1/repos/{repo_url:path}/runs",
    dependencies=[Depends(check_token), Depends(rate_limiter)],
)
@app.get(
    "/api/repos/{repo_url:path}/runs",
    dependencies=[Depends(check_token), Depends(rate_limiter)],
    deprecated=True,
)
async def get_repo_runs(
    repo_url: str,
    limit: int = Query(default=50, ge=1, le=500),
) -> Any:
    """Every run recorded for one repository, across all jobs that analysed it."""
    async with session_scope() as session:
        runs = await store.get_runs_for_repository(session, repo_url, limit=limit)
    return {"repo_url": repo_url, "runs": runs, "total": len(runs)}


@app.get("/api/v1/runs", dependencies=[Depends(check_token), Depends(rate_limiter)])
@app.get(
    "/api/runs",
    dependencies=[Depends(check_token), Depends(rate_limiter)],
    deprecated=True,
)
async def get_runs(
    limit: int = Query(default=50, ge=1, le=500),
    module: str | None = None,
    job_id: JobIdQuery = None,
) -> Any:
    async with session_scope() as session:
        if job_id:
            runs = await store.get_runs_for_job(session, job_id, limit=limit)
        else:
            runs = await store.get_recent_runs(session, limit=limit)
    if module:
        runs = [r for r in runs if module in (r.get("modules_analyzed") or [])]
    return {"runs": runs[:limit], "total": len(runs[:limit])}


async def _with_plain_language(
    run: dict[str, Any], job_id: str | None = None
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
        from archguard.dashboard._selection import select_findings, selection_summary

        out["remediation_selection"] = selection_summary(select_findings(run, job_id))
    except Exception as exc:
        _logger.warning("Could not compute remediation selection: %s", exc)
    return out


@app.get(
    "/api/v1/runs/latest", dependencies=[Depends(check_token), Depends(rate_limiter)]
)
@app.get(
    "/api/runs/latest",
    dependencies=[Depends(check_token), Depends(rate_limiter)],
    deprecated=True,
)
async def get_latest_run(job_id: JobIdQuery = None) -> Any:
    if not job_id:
        return {
            "empty": True,
            "message": "No analysis selected. Submit or select a repository to see health data.",
        }
    async with session_scope() as session:
        run = await store.get_latest_run(session, job_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No run found for job_id {job_id}")
    return await _with_plain_language(run, job_id)


@app.get("/api/v1/modules", dependencies=[Depends(check_token), Depends(rate_limiter)])
@app.get(
    "/api/modules",
    dependencies=[Depends(check_token), Depends(rate_limiter)],
    deprecated=True,
)
async def get_modules(job_id: JobIdQuery = None) -> Any:
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
        run = await store.get_latest_run(session, job_id)

    if run is None:
        return {"empty": True, "modules": {}, "edges": [], "message": "No run found."}

    return {
        "modules": run.get("module_scores") or {},
        # Persisted with the run, so the graph survives the workspace being
        # swept -- it used to be recomputed from the clone, which meant the
        # Dependencies tab went blank the moment the workspace expired.
        "edges": run.get("import_edges") or [],
    }


@app.get(
    "/api/v1/trends/{module}", dependencies=[Depends(check_token), Depends(rate_limiter)]
)
@app.get(
    "/api/trends/{module}",
    dependencies=[Depends(check_token), Depends(rate_limiter)],
    deprecated=True,
)
async def get_module_trends(
    module: str = FastAPIPath(
        ..., min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.]+$"
    ),
    limit: int = Query(default=30, ge=1, le=500),
    job_id: JobIdQuery = None,
) -> Any:
    async with session_scope() as session:
        if job_id:
            runs = await store.get_runs_for_job(session, job_id, limit=limit)
        else:
            runs = await store.get_recent_runs(session, limit=limit)
    trend = [
        {"timestamp": r["timestamp"], "score": (r.get("module_scores") or {}).get(module)}
        for r in runs
        if module in (r.get("module_scores") or {})
    ]
    return {"module": module, "trend": trend}


@app.get("/api/v1/deps", dependencies=[Depends(check_token), Depends(rate_limiter)])
async def get_deps(job_id: JobIdQuery = None) -> Any:
    """Dependency vulnerability scan for one analysis.

    Stored separately from the run: the scan is triggered on demand, after the
    analysis, and folding it into run history is what previously injected a
    bogus entry into the trend chart.
    """
    from archguard.analysis.deps import analyze_dependencies
    from archguard.dashboard.app import get_target_path

    if not job_id:
        raise HTTPException(
            status_code=400, detail="No analysis selected. Submit a job first."
        )

    async with session_scope() as session:
        persisted = await store.get_dependency_scan(session, job_id)
    if persisted is not None:
        return persisted

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
                await store.save_dependency_scan(session, job_id, output)
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
