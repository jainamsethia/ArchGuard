"""Reinference engine integration for AnalysisOrchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _run_reinference(
    repo_root: Path,
    cache: Any,
    audit: Any,
    contract: dict[str, Any],
    affected: dict[str, list[Path]],
    commit_sha: str,
    drift_results: dict[str, float] | None = None,
) -> None:
    """Run reinference staleness check and create proposals if needed."""
    from archguard.analysis.layers import _get_module_paths

    try:
        from archguard.contract.reinference import ReinferenceEngine

        engine = ReinferenceEngine(
            repo_root,
            audit_logger=audit,
        )
        engine.check_staleness()

        # Check each affected module's drift result
        modules_cfg = contract.get("modules", [])
        thresholds: dict[str, float] = {
            m["name"]: m.get("semantic_drift_threshold", 0.25) for m in modules_cfg
        }
        budgets: dict[str, int] = {
            m["name"]: m.get("coupling_budget", 3) for m in modules_cfg
        }
        module_paths: dict[str, str] = {
            m["name"]: _get_module_paths(m)[0] if _get_module_paths(m) else ""
            for m in modules_cfg
        }

        for mod_name in affected:
            threshold = thresholds.get(mod_name, 0.25)

            if drift_results is not None and mod_name in drift_results:
                drift_score = drift_results[mod_name]
            else:
                # We need the drift score - recompute from semantic analyzer
                from archguard.analysis.semantic import SemanticAnalyzer

                analyzer = SemanticAnalyzer(cache)
                try:
                    drift_result = analyzer.compute_drift(
                        mod_name,
                        affected[mod_name],
                        repo_root,
                    )
                    drift_score = drift_result.drift_score
                except RuntimeError as e:
                    if "ML dependencies" in str(e):
                        continue
                    raise
                except Exception as e:
                    from archguard.utils.errors import AnalysisError

                    raise AnalysisError(
                        f"Reinference proposal failed on module {mod_name}", cause=e
                    ) from e

            try:
                if engine.should_propose(mod_name, drift_score, threshold):
                    engine.create_proposal(
                        module_name=mod_name,
                        semantic_drift=drift_score,
                        new_centroid_path=module_paths.get(mod_name, ""),
                        current_coupling_budget=budgets.get(mod_name, 3),
                        source_commit=commit_sha,
                    )
            except Exception as e:
                from archguard.utils.errors import AnalysisError

                raise AnalysisError(
                    f"Reinference proposal failed on module {mod_name}", cause=e
                ) from e
    except Exception as e:
        from archguard.utils.errors import AnalysisError

        raise AnalysisError("Reinference check failed", cause=e) from e
