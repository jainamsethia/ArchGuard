import logging
from collections import deque
from typing import Any

from fastapi import Depends, HTTPException

from archguard.dashboard._auth import check_token
from archguard.dashboard._rate_limit import rate_limiter
from archguard.dashboard.app import JobIdQuery, app, get_target_path

_logger = logging.getLogger(__name__)

# How many transitive dependents a module must have to count as a blast-radius
# "hotspot". Tunable — surfaces the modules whose changes ripple furthest.
_HOTSPOT_THRESHOLD = 3


def _downstream_counts(dependency_graph: dict[str, list[str]]) -> dict[str, int]:
    """For each module, count how many other modules transitively depend on it.

    ``dependency_graph`` maps module -> [modules it depends on]. A change to
    module M ripples to every module whose dependency path reaches M, so we
    invert the graph and BFS outward from each module. This is a structural
    property of the analyzed repo — it does not depend on which files changed.
    """
    inverted: dict[str, list[str]] = {}
    for mod, deps in dependency_graph.items():
        inverted.setdefault(mod, [])
        for dep in deps:
            inverted.setdefault(dep, []).append(mod)

    counts: dict[str, int] = {}
    for src in inverted:
        seen: set[str] = set()
        queue = deque(inverted.get(src, []))
        while queue:
            current = queue.popleft()
            # Skip the module itself. When it sits in a dependency cycle the
            # traversal walks back around to it, and counting it inflated its
            # own blast radius by one -- contradicting "how many *other*
            # modules" above, and letting a cycle push a repo into a higher
            # risk band via max_downstream.
            if current in seen or current == src:
                continue
            seen.add(current)
            queue.extend(inverted.get(current, []))
        counts[src] = len(seen)
    return counts


@app.get("/api/v1/risk", dependencies=[Depends(check_token), Depends(rate_limiter)])
async def get_pr_risk(job_id: JobIdQuery = None) -> Any:
    """Report each module's blast radius for the analyzed repository.

    This is a structural metric computed from the module dependency graph: for
    every module, how many other modules transitively depend on it (and would
    therefore be affected by a change to it). It does NOT require a PR diff —
    the dashboard analyzes a checked-out repo, not a pull request.
    """
    # The persisted graph first: it is what the analysis actually measured,
    # and unlike the clone it is still there after the workspace is swept.
    dependency_graph: dict[str, list[str]] | None = None
    module_names: list[str] | None = None
    if job_id:
        from archguard.db import store
        from archguard.db.session import session_scope

        async with session_scope() as session:
            run = await store.get_latest_run(session, job_id)
        if run and run.get("dependency_graph"):
            dependency_graph = run["dependency_graph"]
            module_names = run.get("modules_analyzed") or list(dependency_graph.keys())

    if dependency_graph is None:
        # Fall back to live computation from filesystem
        try:
            target = get_target_path(job_id)
        except HTTPException:
            raise HTTPException(
                status_code=410,
                detail="Analysis workspace expired. Re-run the analysis to see blast radius data.",
            )
        if not job_id:
            raise HTTPException(
                status_code=400,
                detail="Cannot run blast-radius analysis without a specific repository context.",
            )

        try:
            from archguard.analysis._orchestrator_utils import _get_module_paths
            from archguard.contract.loader import load_contract
            from archguard.fitness.evaluator import FitnessFunctionEvaluator

            contract = load_contract(target)
            module_paths = {
                m["name"]: _get_module_paths(m) for m in contract.get("modules", [])
            }
            module_names = list(module_paths.keys())

            evaluator = FitnessFunctionEvaluator(target, contract)
            dep_set = evaluator._get_module_dependencies()
            dependency_graph = {k: list(v) for k, v in dep_set.items()}
            for name in module_paths:
                dependency_graph.setdefault(name, [])
        except HTTPException:
            raise
        except Exception as e:
            _logger.warning("blast-radius analysis failed: %s", e)
            raise HTTPException(
                status_code=500,
                detail="Could not compute module blast radius. The repository may lack a valid contract or dependency graph.",
            )

    # Ensure every declared module appears in the graph
    if module_names:
        for name in module_names:
            dependency_graph.setdefault(name, [])

    downstream = _downstream_counts(dependency_graph)

    modules: list[dict[str, Any]] = [
        {"module": name, "downstream": downstream.get(name, 0)}
        for name in (module_names or dependency_graph.keys())
    ]
    modules.sort(key=lambda m: m["downstream"], reverse=True)

    hotspots = [m for m in modules if m["downstream"] >= _HOTSPOT_THRESHOLD]
    max_downstream: int = modules[0]["downstream"] if modules else 0
    if max_downstream == 0:
        level = "none"
    elif max_downstream >= 10:
        level = "critical"
    elif max_downstream >= 5:
        level = "high"
    elif max_downstream >= _HOTSPOT_THRESHOLD:
        level = "medium"
    else:
        level = "low"

    return {
        "level": level,
        "modules": modules,
        "hotspots": hotspots,
        "threshold": _HOTSPOT_THRESHOLD,
        "max_downstream": max_downstream,
    }
