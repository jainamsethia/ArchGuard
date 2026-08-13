import os
from typing import Any
from archguard.analysis.layers import ViolationDetail

def _run_layer_4(
    orchestrator: Any,
    violations: list[ViolationDetail],
    affected: Any,
    progress: Any,
    quiet: bool,
    metrics: Any,
    commit_sha: str,
) -> tuple[list[ViolationDetail], float]:
    skip_layers = list(orchestrator.contract.get("skip_layers", []))
    SKIP_ML = os.getenv("ARCHGUARD_SKIP_ML", "").lower() in ("1", "true", "yes")
    if SKIP_ML and "duplication" not in skip_layers:
        skip_layers.append("duplication")

    desc4 = "Layer 4: Duplication Detection..."
    if progress:
        task4 = progress.add_task(desc4, total=None)
    else:
        if not quiet:
            print(desc4)

    if "duplication" in skip_layers:
        layer4 = 0.0
        # As for Layer 3: a skipped layer must not be reported as a clean pass.
        metrics.extra["layer4_skipped"] = True
        metrics.extra["layer4_skip_reason"] = (
            "duplication detection not run (ARCHGUARD_SKIP_ML or contract skip_layers)"
        )
        if progress:
            progress.update(
                task4,
                description="[yellow][!] Layer 4: Skipped (config)[/yellow]",
            )
            progress.stop_task(task4)
        else:
            if not quiet:
                print("[WARN] Layer 4 Skipped (config)")
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
                if progress:
                    progress.update(
                        task4,
                        description=f"[yellow][!] {l4_skip_reason}[/yellow]",
                    )
                    progress.stop_task(task4)
                elif not quiet:
                    print(f"[WARN] {l4_skip_reason}")
            else:
                if progress:
                    progress.update(
                        task4,
                        description=f"[green][OK] Layer 4:[/green] {l4_violations} violations",
                    )
                    progress.stop_task(task4)
                else:
                    if not quiet:
                        print(f"[OK] Layer 4 complete ({l4_violations} violations)")

        except Exception as e:
            if progress:
                progress.stop_task(task4)
            raise RuntimeError(f"Layer 4 analysis failed: {e}") from e

    return violations, layer4

