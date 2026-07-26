"""Run-history, module-list, trend, and dependency-graph read endpoints."""
import logging
from pathlib import Path
from typing import Any
from fastapi import Path as FastAPIPath, Depends, Query, HTTPException
from archguard.dashboard.app import app, get_audit_path, JobIdQuery
from archguard.dashboard._auth import check_token
from archguard.dashboard._rate_limit import rate_limiter
from archguard.audit.logger import AuditLogger
from archguard.config import AUDIT_LOG_FILENAME

_logger = logging.getLogger(__name__)

@app.get("/api/v1/repos/{repo_url:path}/runs", dependencies=[Depends(check_token), Depends(rate_limiter)])
@app.get("/api/repos/{repo_url:path}/runs", dependencies=[Depends(check_token), Depends(rate_limiter)], deprecated=True)
def get_repo_runs(
    repo_url: str,
    limit: int = Query(default=50, ge=1, le=500),
) -> Any:
    """Return run history for one repository, across all jobs that ever analyzed it."""
    logger = AuditLogger(Path.cwd() / AUDIT_LOG_FILENAME)  # always the global log -- this view is explicitly cross-job by design
    runs = logger.read_last_n_runs(n=500)  # read generously, then filter+limit, since read_last_n_runs has no repo_url-aware filtering itself
    matching = [r for r in runs if r.get("repo_url") == repo_url][:limit]
    return {"repo_url": repo_url, "runs": matching, "total": len(matching)}


@app.get("/api/v1/runs", dependencies=[Depends(check_token), Depends(rate_limiter)])
@app.get("/api/runs", dependencies=[Depends(check_token), Depends(rate_limiter)], deprecated=True)
def get_runs(
    limit: int = Query(default=50, ge=1, le=500), module: str | None = None, job_id: JobIdQuery = None
) -> Any:
    logger = AuditLogger(get_audit_path(job_id))
    # When filtering by job_id, read a generous number of runs so that
    # the filter doesn't silently lose results (the naive approach of
    # reading only `limit` lines then applying the filter misses entries
    # deep in the log that happen to be older than the most recent N).
    read_limit = max(limit, 10000) if (job_id or module) else limit
    runs = logger.read_last_n_runs(n=read_limit)
    if job_id:
        runs = [r for r in runs if r.get("job_id") == job_id]
    if module:
        runs = [r for r in runs if module in r.get("modules_analyzed", [])]
    return {"runs": runs[:limit], "total": len(runs[:limit])}


@app.get("/api/v1/runs/latest", dependencies=[Depends(check_token), Depends(rate_limiter)])
@app.get("/api/runs/latest", dependencies=[Depends(check_token), Depends(rate_limiter)], deprecated=True)
def get_latest_run(job_id: JobIdQuery = None) -> Any:
    from fastapi import HTTPException
    logger = AuditLogger(get_audit_path(job_id))
    if job_id:
        runs = logger.read_last_n_runs(n=100)
        # read_last_n_runs returns chronological order (oldest first).
        # Iterate in reverse so we return the *newest* match for this job_id.
        for r in reversed(runs):
            if r.get("job_id") == job_id:
                return r
        raise HTTPException(status_code=404, detail=f"No run found for job_id {job_id}")
    return {"empty": True, "message": "No analysis selected. Submit or select a repository to see health data."}


class ParsedEdge:
    def __init__(self, importer: str, imported: str):
        self.importer_module = importer
        self.imported_module = imported

def _edges_from_audit(job_id: str | None) -> list[ParsedEdge] | None:
    """Try to read pre-computed import_edges from the audit log. Returns None if unavailable."""
    if not job_id:
        return None
    logger = AuditLogger(get_audit_path(job_id))
    runs = logger.read_last_n_runs(n=100)
    for r in reversed(runs):
        if r.get("job_id") == job_id and r.get("import_edges"):
            return [
                ParsedEdge(e["from"], e["to"])
                for e in r["import_edges"]
                if isinstance(e, dict) and "from" in e and "to" in e
            ]
    return None

def get_import_edges(job_id: JobIdQuery = None) -> list[ParsedEdge]:
    # Prefer pre-computed edges from audit log (survive workspace deletion)
    cached = _edges_from_audit(job_id)
    if cached is not None:
        return cached

    from archguard.analysis.parser import ImportParser
    from archguard.dashboard.app import get_target_path
    from archguard.contract.loader import load_contract
    from archguard.analysis._orchestrator_utils import _get_module_paths
    from archguard.analysis.coupling import _assign_file_to_module
    from archguard.utils.paths import path_belongs_to_module

    try:
        target = get_target_path(job_id)
    except HTTPException:
        return []

    try:
        contract = load_contract(target)
        modules_cfg = contract.get("modules", [])
        module_paths = {m["name"]: _get_module_paths(m) for m in modules_cfg}

        parser = ImportParser()
        parse_result = parser.parse_repo(target, module_paths, allow_partial=True)

        edges_out = []
        seen = set()
        for e in parse_result.edges:
            if e.is_stdlib or e.is_relative:
                continue
            importer = _assign_file_to_module(e.source_file, module_paths)
            if not importer:
                continue

            import_as_path = e.imported_module.replace(".", "/")
            imported = None
            for tp_name, t_paths in module_paths.items():
                targets_us = any(
                    path_belongs_to_module(import_as_path, [tp])
                    or path_belongs_to_module(tp, [import_as_path])
                    for tp in t_paths
                )
                if targets_us:
                    imported = tp_name
                    break

            if not imported:
                imported = e.imported_module.split(".")[0]

            if importer != imported:
                k = (importer, imported)
                if k not in seen:
                    seen.add(k)
                    edges_out.append(ParsedEdge(importer, imported))

        return edges_out
    except Exception as exc:
        _logger.warning("get_import_edges failed for job_id=%s: %s", job_id, exc)
        return []

@app.get("/api/v1/modules", dependencies=[Depends(check_token), Depends(rate_limiter)])
@app.get("/api/modules", dependencies=[Depends(check_token), Depends(rate_limiter)], deprecated=True)
def get_modules(job_id: JobIdQuery = None) -> Any:
    """Return all known modules and their latest scores."""
    if not job_id:
        return {"empty": True, "modules": {}, "edges": []}
    logger = AuditLogger(get_audit_path(job_id))
    read_n = 10000 if job_id else 100
    runs = logger.read_last_n_runs(n=read_n)
    if job_id:
        runs = [r for r in runs if r.get("job_id") == job_id]
    modules = {}
    for run in runs:
        for module, score in run.get("module_scores", {}).items():
            modules[module] = score  # latest score wins
            
    edges = [
        {"from": e.importer_module, "to": e.imported_module}
        for e in get_import_edges(job_id)
    ]
    return {"modules": modules, "edges": edges}


@app.get(
    "/api/v1/trends/{module}", dependencies=[Depends(check_token), Depends(rate_limiter)]
)
@app.get(
    "/api/trends/{module}", dependencies=[Depends(check_token), Depends(rate_limiter)], deprecated=True
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
    if job_id:
        runs = [r for r in runs if r.get("job_id") == job_id]
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
    from archguard.dashboard.app import get_target_path, get_audit_path
    import json

    if not job_id:
        raise HTTPException(status_code=400, detail="No analysis selected. Submit a job first.")

    audit_file = get_audit_path(job_id)
    logger_instance = AuditLogger(audit_file)
    runs = logger_instance.read_last_n_runs(n=100)
    for r in reversed(runs):
        if r.get("job_id") == job_id and "dependency_health" in r:
            return r["dependency_health"]

    try:
        target = get_target_path(job_id)
    except HTTPException:
        return {
            "skipped": True,
            "skip_reason": "Analysis workspace expired. Re-run the analysis to scan dependencies."
        }

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

        if audit_file.exists():
            try:
                lines = audit_file.read_text(encoding="utf-8").splitlines()
                for i in range(len(lines) - 1, -1, -1):
                    if not lines[i].strip():
                        continue
                    entry = json.loads(lines[i])
                    if entry.get("job_id") == job_id and entry.get("event") == "analysis_complete":
                        entry["dependency_health"] = output
                        lines[i] = json.dumps(entry)
                        audit_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                        break
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("Failed to persist dependency scan: %s", e)

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
