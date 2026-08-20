import os
from collections.abc import Callable
from typing import Any

from archguard.analysis.layers import ViolationDetail

#: See _orchestrator_run.EmitFn -- reports a stage transition.
EmitFn = Callable[[str], None]


def _run_layer_4(
    orchestrator: Any,
    violations: list[ViolationDetail],
    affected: Any,
    emit: EmitFn,
    metrics: Any,
    commit_sha: str,
) -> tuple[list[ViolationDetail], float]:
    skip_layers = list(orchestrator.contract.get("skip_layers", []))
    SKIP_ML = os.getenv("ARCHGUARD_SKIP_ML", "").lower() in ("1", "true", "yes")
    if SKIP_ML and "duplication" not in skip_layers:
        skip_layers.append("duplication")

    emit("Layer 4: duplication detection...")

    if "duplication" in skip_layers:
        layer4 = 0.0
        # As for Layer 3: a skipped layer must not be reported as a clean pass.
        metrics.extra["layer4_skipped"] = True
        metrics.extra["layer4_skip_reason"] = (
            "duplication detection not run (ARCHGUARD_SKIP_ML or contract skip_layers)"
        )
        emit("Layer 4 skipped (ARCHGUARD_SKIP_ML or contract skip_layers).")
    else:
        try:
            with metrics.time_layer("layer4"):
                from archguard.analysis._layer_runners import _run_layer4

                layer4, l4_viols, l4_skip_reason = _run_layer4(
                    orchestrator.repo_root,
                    orchestrator.cache,
                    orchestrator.contract,
                    affected,
                    commit_sha,
                )
                violations.extend(l4_viols)
            l4_violations = len(l4_viols)

            if l4_skip_reason:
                metrics.extra["layer4_skipped"] = True
                metrics.extra["layer4_skip_reason"] = l4_skip_reason
                emit(f"Layer 4 skipped: {l4_skip_reason}")
            else:
                emit(f"Layer 4 complete: {l4_violations} violation(s).")

        except Exception as e:
            raise RuntimeError(f"Layer 4 analysis failed: {e}") from e

    return violations, layer4

