from dataclasses import dataclass
from typing import Any


@dataclass
class TrendAlert:
    metric: str  # e.g., "archdebt_score" or "module_score"
    module: str | None  # module name or None for overall
    direction: str  # "degrading" or "improving"
    delta: float  # magnitude of change
    window: int  # number of runs analyzed
    message: str


def detect_trends(
    runs: list[dict[str, Any]],
    window: int = 10,
    degradation_threshold: float = 0.05,  # 5 point change
) -> list[TrendAlert]:
    if len(runs) < window:
        return []

    alerts = []
    recent = runs[-window:]

    # Overall score trend
    first_score = recent[0].get("score", 0.0)
    last_score = recent[-1].get("score", 0.0)
    delta = last_score - first_score

    if abs(delta) >= degradation_threshold:
        direction = "degrading" if delta > 0 else "improving"
        alerts.append(
            TrendAlert(
                metric="archdebt_score",
                module=None,
                direction=direction,
                delta=abs(delta),
                window=window,
                message=f"Overall ArchDebt {direction} by {abs(delta):.3f} over last {window} runs",
            )
        )

    # Per-module trends
    first_modules = recent[0].get("module_scores", {})
    last_modules = recent[-1].get("module_scores", {})

    for mod, first_mod_score in first_modules.items():
        if mod in last_modules:
            last_mod_score = last_modules[mod]
            mod_delta = last_mod_score - first_mod_score
            if abs(mod_delta) >= degradation_threshold:
                mod_direction = "degrading" if mod_delta > 0 else "improving"
                alerts.append(
                    TrendAlert(
                        metric="module_score",
                        module=mod,
                        direction=mod_direction,
                        delta=abs(mod_delta),
                        window=window,
                        message=f"Module '{mod}' {mod_direction} by {abs(mod_delta):.3f} over last {window} runs",
                    )
                )

    return alerts
