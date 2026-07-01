"""Run-history, module-list, trend, and dependency-graph read endpoints."""

from typing import Any
from fastapi import Path as FastAPIPath, Depends, Query
from archguard.dashboard.app import app, get_audit_path, JobIdQuery
from archguard.dashboard._auth import check_token
from archguard.dashboard._rate_limit import rate_limiter
from archguard.audit.logger import AuditLogger


@app.get("/api/runs", dependencies=[Depends(check_token), Depends(rate_limiter)])
def get_runs(
    limit: int = Query(default=50, ge=1, le=500), module: str | None = None, job_id: JobIdQuery = None
) -> Any:
    logger = AuditLogger(get_audit_path(job_id))
    runs = logger.read_last_n_runs(n=limit)
    if module:
        runs = [r for r in runs if module in r.get("modules_analyzed", [])]
    return {"runs": runs, "total": len(runs)}


@app.get("/api/runs/latest", dependencies=[Depends(check_token), Depends(rate_limiter)])
def get_latest_run(job_id: JobIdQuery = None) -> Any:
    logger = AuditLogger(get_audit_path(job_id))
    if job_id:
        runs = logger.read_last_n_runs(n=100)
        for r in runs:
            if r.get("job_id") == job_id:
                return r
    return logger.read_last_run() or {}


@app.get("/api/modules", dependencies=[Depends(check_token), Depends(rate_limiter)])
def get_modules(job_id: JobIdQuery = None) -> Any:
    """Return all known modules and their latest scores."""
    logger = AuditLogger(get_audit_path(job_id))
    runs = logger.read_last_n_runs(n=100)
    modules = {}
    for run in runs:
        for module, score in run.get("module_scores", {}).items():
            modules[module] = score  # latest score wins
    return {"modules": modules}


@app.get(
    "/api/trends/{module}", dependencies=[Depends(check_token), Depends(rate_limiter)]
)
def get_module_trends(
    module: str = FastAPIPath(
        ..., min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_\-\.]+$"
    ),
    limit: int = Query(default=30, ge=1, le=500),
    job_id: JobIdQuery = None
) -> Any:
    logger = AuditLogger(get_audit_path(job_id))
    runs = logger.read_last_n_runs(n=limit)
    trend = [
        {"timestamp": r["timestamp"], "score": r.get("module_scores", {}).get(module)}
        for r in runs
        if module in r.get("module_scores", {})
    ]
    return {"module": module, "trend": trend}


@app.get("/api/v1/deps", dependencies=[Depends(check_token), Depends(rate_limiter)])
def get_deps(job_id: JobIdQuery = None) -> Any:
    """Run dependency analysis and return the result."""
    from archguard.analysis.deps import analyze_dependencies
    from archguard.dashboard.app import get_target_path

    target = get_target_path(job_id)
    if job_id and target == Path.cwd():
        return {
            "score": 0.0,
            "vulnerable_packages": [],
            "scanned_packages": 0,
            "skipped": True,
            "skip_reason": "Repository workspace has been cleaned up. Run analysis again to scan dependencies.",
            "error": "Workspace deleted",
        }

    try:
        result = analyze_dependencies(target)

        return {
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
    except Exception as e:
        return {
            "score": 0.0,
            "vulnerable_packages": [],
            "scanned_packages": 0,
            "skipped": True,
            "skip_reason": "Exception during analysis",
            "error": str(e),
        }
