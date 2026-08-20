from dataclasses import dataclass
from typing import Any


@dataclass
class TrendAlert:
    metric: str  # "health_score" or "module_score"
    module: str | None  # module name or None for overall
    direction: str  # "degrading" or "improving"
    delta: float  # magnitude of change, always non-negative
    window: int  # number of runs analyzed
    message: str


def _direction(delta: float) -> str:
    """Classify a change in a *health* score, where higher is better.

    Both scores this module compares -- the run's ``score`` and each entry in
    ``module_scores`` -- are 0-100 health scores produced by
    ``ArchDebtResult.health_score``, which is ``(1 - composite) * 100``. A
    positive delta is therefore an improvement.

    This read the other way round until 2026-08-20, so a repository whose
    health rose from 50 to 90 was alerted on as "degrading by 40". The name
    ``archdebt_score`` is what invited the mistake: ArchDebt is indeed
    lower-is-better, but the value carried here is its inverse.
    """
    return "improving" if delta > 0 else "degrading"


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
        direction = _direction(delta)
        alerts.append(
            TrendAlert(
                metric="health_score",
                module=None,
                direction=direction,
                delta=abs(delta),
                window=window,
                message=(
                    f"Overall health {direction} by {abs(delta):.1f} points "
                    f"over the last {window} runs"
                ),
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
                mod_direction = _direction(mod_delta)
                alerts.append(
                    TrendAlert(
                        metric="module_score",
                        module=mod,
                        direction=mod_direction,
                        delta=abs(mod_delta),
                        window=window,
                        message=(
                            f"Module '{mod}' {mod_direction} by "
                            f"{abs(mod_delta):.1f} points over the last {window} runs"
                        ),
                    )
                )

    return alerts
