"""Git-history evolution analysis endpoints."""

import logging
import threading
from typing import Any
from pydantic import BaseModel, Field
from fastapi import Depends, Query, HTTPException
from archguard.dashboard.app import app, get_audit_path, JobIdQuery
from archguard.dashboard._auth import check_token
from archguard.dashboard._rate_limit import rate_limiter
from archguard.audit.logger import AuditLogger


@app.get(
    "/api/v1/evolution/summary", dependencies=[Depends(check_token), Depends(rate_limiter)]
)
@app.get(
    "/api/evolution/summary", dependencies=[Depends(check_token), Depends(rate_limiter)], deprecated=True
)
def get_evolution_summary(limit: int = Query(default=50, ge=1, le=500), job_id: JobIdQuery = None) -> Any:
    """Return the complete evolution trend report."""
    from archguard.evolution.tracker import EvolutionTracker

    logger = AuditLogger(get_audit_path(job_id))
    runs = logger.read_last_n_runs(n=limit)
    tracker = EvolutionTracker(runs)
    report = tracker.generate_report()
    return report.model_dump() if hasattr(report, "model_dump") else report.dict()


@app.get(
    "/api/v1/evolution/history", dependencies=[Depends(check_token), Depends(rate_limiter)]
)
@app.get(
    "/api/evolution/history", dependencies=[Depends(check_token), Depends(rate_limiter)], deprecated=True
)
def get_evolution_history(limit: int = Query(default=50, ge=1, le=500), job_id: JobIdQuery = None) -> Any:
    """Return the parsed evolution snapshots."""
    from archguard.evolution.tracker import EvolutionTracker

    logger = AuditLogger(get_audit_path(job_id))
    runs = logger.read_last_n_runs(n=limit)
    tracker = EvolutionTracker(runs)
    snapshots = [
        s.model_dump() if hasattr(s, "model_dump") else s.dict()
        for s in tracker.snapshots
    ]
    return {"history": snapshots, "total": len(snapshots)}


@app.get(
    "/api/v1/evolution/trends", dependencies=[Depends(check_token), Depends(rate_limiter)]
)
@app.get(
    "/api/evolution/trends", dependencies=[Depends(check_token), Depends(rate_limiter)], deprecated=True
)
def get_evolution_trends(limit: int = Query(default=50, ge=1, le=500), job_id: JobIdQuery = None) -> Any:
    """Return just the calculated trends."""
    from archguard.evolution.tracker import EvolutionTracker

    logger = AuditLogger(get_audit_path(job_id))
    runs = logger.read_last_n_runs(n=limit)
    tracker = EvolutionTracker(runs)
    report = tracker.generate_report()

    def dump_trend(t: Any) -> Any:
        if not t:
            return None
        return t.model_dump() if hasattr(t, "model_dump") else t.dict()

    return {
        "health_trend": dump_trend(report.health_trend),
        "violation_trend": dump_trend(report.violation_trend),
        "debt_trend": dump_trend(report.debt_trend),
        "fitness_trend": dump_trend(report.fitness_trend),
    }


# -----------------------------------------------------------------------------
# Architecture Evolution (Git History) Endpoints
# -----------------------------------------------------------------------------

_EVO_LOCK = threading.Lock()
_EVO_CACHE: dict[str, Any] = {}


class EvolutionAnalyzeRequest(BaseModel):
    max_commits: int = Field(default=5, ge=1, le=100)


@app.post(
    "/api/v1/evolution/analyze", dependencies=[Depends(check_token), Depends(rate_limiter)]
)
@app.post(
    "/api/evolution/analyze", dependencies=[Depends(check_token), Depends(rate_limiter)], deprecated=True
)
def start_evolution(body: EvolutionAnalyzeRequest, job_id: JobIdQuery = None) -> Any:
    """Run ArchitectureEvolutionTracker against git history."""
    from archguard.evolution.tracker import ArchitectureEvolutionTracker
    from archguard.dashboard.app import get_target_path

    try:
        target = get_target_path(job_id)
    except HTTPException:
        return {
            "error": "workspace_expired",
            "message": "Analysis workspace expired. Re-run the analysis to examine git history.",
            "snapshots": [],
            "commits_analyzed": 0,
        }

    # Detect shallow clone — git history features are meaningless with --depth=1
    shallow_marker = target / ".git" / "shallow"
    if shallow_marker.exists():
        return {
            "error": "shallow_clone",
            "message": "This repository was analyzed with a shallow clone. Git-history trends require full history.",
            "snapshots": [],
            "commits_analyzed": 0,
        }

    try:
        tracker = ArchitectureEvolutionTracker(target)
        report = tracker.analyze_history(max_commits=body.max_commits)

        result = {
            "snapshots": [
                {
                    "sha": s.sha,
                    "committed_at": s.committed_at,
                    "health_score": s.health_score,
                    "violation_count": s.violation_count,
                    "author": s.author,
                    "message": s.message,
                }
                for s in report.snapshots
            ],
            "debt_velocity": report.debt_velocity,
            "trend_direction": report.trend_direction,
            "score_range": {"min": report.score_range[0], "max": report.score_range[1]},
            "commits_analyzed": len(report.snapshots),
        }

        with _EVO_LOCK:
            cache_key = job_id or "_no_job_id"
            _EVO_CACHE[cache_key] = result

        return result
    except Exception as exc:
        logging.error("Evolution analysis failed: %s", exc)
        return {"error": "analysis_failed", "message": "Could not analyze git history.", "snapshots": [], "commits_analyzed": 0}


@app.get(
    "/api/v1/evolution/latest", dependencies=[Depends(check_token), Depends(rate_limiter)]
)
@app.get(
    "/api/evolution/latest", dependencies=[Depends(check_token), Depends(rate_limiter)], deprecated=True
)
def get_latest_evolution(job_id: JobIdQuery = None) -> Any:
    """Get the latest completed architecture evolution report."""
    with _EVO_LOCK:
        cache_key = job_id or "_no_job_id"
        if cache_key in _EVO_CACHE:
            return _EVO_CACHE[cache_key]
    return {"available": False}
