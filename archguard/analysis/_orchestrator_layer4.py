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
        if progress:
            progress.update(
                task4,
                description="[yellow]⚠ Layer 4: Skipped (config)[/yellow]",
            )
            progress.stop_task(task4)
        else:
            if not quiet:
                print("⚠ Layer 4 Skipped (config)")
    else:
        try:
            with metrics.time_layer("layer4"):
                from archguard.analysis._layer_runners import _run_layer4

                layer4, l4_viols = _run_layer4(
                    orchestrator.repo_root,
                    orchestrator.cache,
                    orchestrator.contract,
                    affected,
                    commit_sha,
                )
                violations.extend(l4_viols)
            l4_violations = len(l4_viols)
            if progress:
                progress.update(
                    task4,
                    description=f"[green]✓ Layer 4:[/green] {l4_violations} violations",
                )
                progress.stop_task(task4)
            else:
                if not quiet:
                    print(f"[OK] Layer 4 complete ({l4_violations} violations)")
        except RuntimeError as e:
            if "ML dependencies" in str(e):
                if progress:
                    progress.update(
                        task4,
                        description="[bold red]✗ Layer 4: Failed (Missing ML dependencies)[/bold red]",
                    )
                    progress.stop_task(task4)
                raise
            else:
                raise

    return violations, layer4

