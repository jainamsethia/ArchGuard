"""ArchDebt scoring pipeline and band classification."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import numpy.typing as npt


class ArchDebtBand(str, Enum):
    """Classification bands for ArchDebt composite score."""

    HEALTHY = "Healthy"    # score < warn_threshold
    WATCH = "Watch"        # warn_threshold <= score < fail_threshold
    WARN = "Warn"          # score >= fail_threshold (soft)
    CRITICAL = "Critical"  # score >= fail_threshold (composite breach)


@dataclass
class LayerScores:
    """Raw scores for each analysis layer (0.0–1.0)."""

    layer1_violation: float    # import boundary violation ratio
    layer2_coupling: float     # CouplingDelta max across modules
    layer3_drift: float        # SemanticDrift max across modules
    layer4_duplication: float  # DuplicationResult aggregate max


@dataclass
class ArchDebtResult:
    """Complete ArchDebt scoring result."""

    composite_score: float
    band: ArchDebtBand
    layer_scores: LayerScores
    weights: tuple[float, float, float, float]
    per_component_breach: bool     # any single layer > its threshold
    composite_breach: bool         # composite_score >= fail_threshold
    should_fail_ci: bool           # per_component_breach OR composite_breach
    fail_reasons: list[str] = field(default_factory=list)


DEFAULT_WEIGHTS: tuple[float, float, float, float] = (0.25, 0.25, 0.25, 0.25)

_LAYER_NAMES: dict[str, str] = {
    "layer1": "import violations",
    "layer2": "coupling delta",
    "layer3": "semantic drift",
    "layer4": "duplication",
}


def classify_band(
    score: float,
    warn_threshold: float,
    fail_threshold: float,
) -> ArchDebtBand:
    """Classify a composite score into a band."""
    if score >= fail_threshold:
        return ArchDebtBand.CRITICAL
    if score >= warn_threshold:
        return ArchDebtBand.WATCH
    return ArchDebtBand.HEALTHY


def compute_archdebt(
    scores: LayerScores,
    weights: tuple[float, float, float, float] = DEFAULT_WEIGHTS,
    fail_threshold: float = 0.75,
    warn_threshold: float = 0.50,
    per_component_thresholds: dict[str, float] | None = None,
) -> ArchDebtResult:
    """Compute composite ArchDebt score and determine CI outcome.

    ``composite = w1*layer1 + w2*layer2 + w3*layer3 + w4*layer4``

    Per-component breach: any layer score > its per_component_threshold.
    ``should_fail_ci = composite_breach OR per_component_breach``.
    """
    layer_values = [
        scores.layer1_violation,
        scores.layer2_coupling,
        scores.layer3_drift,
        scores.layer4_duplication,
    ]

    composite = sum(w * v for w, v in zip(weights, layer_values))
    composite = float(np.clip(composite, 0.0, 1.0))

    band = classify_band(composite, warn_threshold, fail_threshold)
    composite_breach = composite >= fail_threshold

    # Per-component check
    thresholds = per_component_thresholds or {}
    layer_keys = ["layer1", "layer2", "layer3", "layer4"]
    per_component_breach = False
    fail_reasons: list[str] = []

    if composite_breach:
        fail_reasons.append(
            f"Composite ArchDebt {composite:.2f} exceeds "
            f"fail_threshold {fail_threshold:.2f}"
        )

    for key, value in zip(layer_keys, layer_values):
        threshold = thresholds.get(key, fail_threshold)
        if value > threshold:
            per_component_breach = True
            layer_label = _LAYER_NAMES.get(key, key)
            fail_reasons.append(
                f"Layer {key[-1]} ({layer_label}) score {value:.2f} "
                f"exceeds threshold {threshold:.2f}"
            )

    should_fail_ci = composite_breach or per_component_breach

    return ArchDebtResult(
        composite_score=composite,
        band=band,
        layer_scores=scores,
        weights=weights,
        per_component_breach=per_component_breach,
        composite_breach=composite_breach,
        should_fail_ci=should_fail_ci,
        fail_reasons=fail_reasons,
    )


def calibrate_weights(
    historical_scores: list[LayerScores],
    target_composite: list[float],
) -> tuple[float, float, float, float]:
    """Fit weights via NNLS. Normalize to sum=1.0.

    Falls back to ``DEFAULT_WEIGHTS`` on any failure.
    """
    if not historical_scores:
        return DEFAULT_WEIGHTS

    try:
        from scipy.optimize import nnls as _nnls  # lazy import

        a_matrix = np.array(
            [
                [
                    s.layer1_violation,
                    s.layer2_coupling,
                    s.layer3_drift,
                    s.layer4_duplication,
                ]
                for s in historical_scores
            ],
            dtype=np.float64,
        )
        b_vec = np.array(target_composite, dtype=np.float64)
        w, _ = _nnls(a_matrix, b_vec)
        total = float(w.sum())
        if total == 0:
            return DEFAULT_WEIGHTS
        w = w / total
        return (float(w[0]), float(w[1]), float(w[2]), float(w[3]))
    except Exception:  # noqa: BLE001
        return DEFAULT_WEIGHTS
