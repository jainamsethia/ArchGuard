"""ArchDebt scoring pipeline and band classification."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    import numpy as np
    import numpy.typing as npt
    from scipy.optimize import nnls as _nnls

    _ML_AVAILABLE = True
except ImportError:
    _ML_AVAILABLE = False
    import typing

    np: typing.Any = None  # type: ignore[no-redef]
    npt: typing.Any = None  # type: ignore[no-redef]
    _nnls: typing.Any = None  # type: ignore[no-redef]


class ArchDebtBand(str, Enum):
    """Classification bands for ArchDebt composite score.

    Boundaries are set by classify_band() against the composite score:
    HEALTHY:  score <  warn_threshold / 2
    WATCH:    warn_threshold / 2  <= score < warn_threshold
    WARN:     warn_threshold      <= score < fail_threshold
    CRITICAL: score >= fail_threshold
    """

    HEALTHY = "Healthy"  # score < warn_threshold / 2
    WATCH = "Watch"  # warn_threshold / 2 <= score < warn_threshold
    WARN = "Warn"  # warn_threshold <= score < fail_threshold
    CRITICAL = "Critical"  # score >= fail_threshold


# Grades from best to worst; used to take the worse of two grades when capping.
_GRADE_ORDER: tuple[str, ...] = ("A", "B", "C", "D", "F")

# A failed critical fitness gate can never report better than this, whatever the
# contract's thresholds say. "C" is the first grade that is not A/B: the point is
# that a repo with e.g. a real import cycle must not be presented as well-built,
# not to assign it a specific numeric penalty.
_FITNESS_FAILURE_GRADE_CEILING = "C"


def _grade_for_health(health: float) -> str:
    """Letter grade for a 0-100 health score (higher = better)."""
    if health >= 90:
        return "A"
    if health >= 80:
        return "B"
    if health >= 70:
        return "C"
    if health >= 60:
        return "D"
    return "F"


def _worse_grade(a: str, b: str) -> str:
    return a if _GRADE_ORDER.index(a) >= _GRADE_ORDER.index(b) else b


@dataclass
class LayerScores:
    """Raw scores for each analysis layer (0.0–1.0)."""

    layer1_violation: float  # import boundary violation ratio
    layer2_coupling: float  # CouplingDelta max across modules
    layer3_drift: float  # SemanticDrift max across modules
    layer4_duplication: float  # DuplicationResult aggregate max


@dataclass
class ArchDebtResult:
    """Complete ArchDebt scoring result."""

    composite_score: float
    band: ArchDebtBand
    layer_scores: LayerScores
    weights: tuple[float, float, float, float]
    per_component_breach: bool  # any single layer > its threshold
    composite_breach: bool  # composite_score >= fail_threshold
    should_fail_ci: bool  # per_component_breach OR composite_breach
    fail_reasons: list[str] = field(default_factory=list)
    fitness_results: list[Any] = field(default_factory=list)
    fitness_passed: bool = True
    # The contract's own fail_threshold, retained so the band/grade cap below
    # can be expressed in the contract's terms instead of a fresh constant.
    fail_threshold: float = 0.75

    def apply_fitness_results(
        self, fitness_results: list[Any], configs: list[Any] | None = None
    ) -> None:
        """Apply evaluated fitness results to this ArchDebtResult.

        A failed *critical* gate caps the reported band and grade; it does not
        move ``composite_score``. The composite is a measurement of layer debt,
        and overwriting it with a fixed penalty (the previous behaviour: floor
        the composite at 0.75) reported a precise-looking health score that no
        layer had actually measured -- every such repo came out at exactly 25.0.
        The debt stays what it was; what changes is that the result may no
        longer be *presented* as healthy.
        """
        self.fitness_results = fitness_results
        config_map: dict[str, Any] = {}
        if configs:
            for c in configs:
                config_map[getattr(c, "rule", "")] = c

        for fr in fitness_results:
            rule = getattr(fr, "rule", "")
            passed = getattr(fr, "passed", True)
            cfg = config_map.get(rule)
            severity = getattr(cfg, "severity", "warn") if cfg else "warn"
            name = getattr(cfg, "name", rule) if cfg else rule

            if not passed and severity == "critical":
                self.should_fail_ci = True
                self.fitness_passed = False
                details = (
                    getattr(fr, "details", None) or getattr(fr, "error", None) or ""
                )
                self.fail_reasons.append(
                    f"Fitness function '{name}' FAILED (critical): {details}"
                )

        if not self.fitness_passed:
            # Cap the band, don't touch the score. Gated on fitness_passed and
            # not should_fail_ci: the latter is already True whenever a layer
            # threshold was breached, so keying off it capped runs where every
            # fitness function had actually passed.
            #
            # composite_breach is deliberately left alone -- it means "the
            # composite crossed fail_threshold", which is still false here.
            # should_fail_ci is set above, which is what actually gates CI.
            self.band = ArchDebtBand.CRITICAL

    @property
    def health_score(self) -> float:
        """Health score 0–100 where HIGHER = BETTER. Inverse of composite debt.
        composite_score is 0.0–1.0 where HIGHER = WORSE.
        health_score = (1.0 - composite_score) * 100
        Examples:
        composite_score=0.125 -> health_score=87.5  (healthy)
        composite_score=0.750 -> health_score=25.0  (unhealthy)
        """
        return round((1.0 - self.composite_score) * 100, 1)

    @property
    def health_grade(self) -> str:
        """Letter grade A/B/C/D/F based on health_score (higher = better).

        Capped when a critical fitness gate failed. The cap is the worse of:

        * the best grade a CRITICAL-band run could hold under this contract --
          ``(1 - fail_threshold) * 100`` is by definition the highest health
          still classified CRITICAL, so the cap follows the contract's own
          thresholds rather than a number invented here; and
        * ``C``, so that a contract with a very lenient fail_threshold can
          still never present a cycle-carrying repo as an A or a B.
        """
        grade = _grade_for_health(self.health_score)
        if not self.fitness_passed:
            threshold_ceiling = _grade_for_health(
                round((1.0 - self.fail_threshold) * 100, 1)
            )
            grade = _worse_grade(
                grade, _worse_grade(threshold_ceiling, _FITNESS_FAILURE_GRADE_CEILING)
            )
        return grade


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
    elif score >= warn_threshold:
        return ArchDebtBand.WARN
    elif score >= warn_threshold / 2.0:
        return ArchDebtBand.WATCH
    return ArchDebtBand.HEALTHY


def compute_archdebt(
    scores: LayerScores,
    weights: tuple[float, float, float, float] = DEFAULT_WEIGHTS,
    fail_threshold: float = 0.75,
    warn_threshold: float = 0.50,
    per_component_thresholds: dict[str, float] | None = None,
    skipped: list[str] | None = None,
) -> ArchDebtResult:
    """Compute composite ArchDebt score and determine CI outcome.

    ``composite = w1*layer1 + w2*layer2 + w3*layer3 + w4*layer4``

    Per-component breach: any layer score > its per_component_threshold.
    ``should_fail_ci = composite_breach OR per_component_breach``.
    """
    layer_map = {
        "Layer 1": scores.layer1_violation,
        "Layer 2": scores.layer2_coupling,
        "Layer 3": scores.layer3_drift,
        "Layer 4": scores.layer4_duplication,
    }

    skipped_names = skipped or []
    active = {k: v for k, v in layer_map.items() if k not in skipped_names}

    if not active:
        # Nothing was measured. An average over no layers is arithmetically
        # 0.00, and 0.00 debt is 100/100 and a passing band -- so a repository
        # whose contract matched no file, on which not one check could run, came
        # back perfect.
        #
        # 1.0 rather than 0.0 because those are the only two answers available
        # and one of them is a claim the run cannot support. This is the same
        # convention `_skip_payload` already uses for the analogous case ("No
        # Python files found in repository"): score 0, band FAIL, and a reason
        # carried alongside. The caller sets `AnalysisResult.skipped` with that
        # reason, and the dashboard shows it as "not checked" per layer rather
        # than as a verdict on the code.
        raw_score = 1.0
    else:
        weight_each = 1.0 / len(active)
        raw_score = sum(weight_each * s for s in active.values())

    composite = float(max(0.0, min(1.0, raw_score)))

    band = classify_band(composite, warn_threshold, fail_threshold)
    composite_breach = composite >= fail_threshold

    # Per-component check
    thresholds = per_component_thresholds or {}
    layer_keys = ["layer1", "layer2", "layer3", "layer4"]
    layer_values = [
        scores.layer1_violation,
        scores.layer2_coupling,
        scores.layer3_drift,
        scores.layer4_duplication,
    ]
    per_component_breach = False
    fail_reasons: list[str] = []

    if composite_breach:
        fail_reasons.append(
            f"Composite ArchDebt {composite:.2f} exceeds "
            f"fail_threshold {fail_threshold:.2f}"
        )

    for key, value in zip(layer_keys, layer_values, strict=True):
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
        fail_threshold=fail_threshold,
    )


def calibrate_weights(
    historical_scores: list[LayerScores],
    target_composite: list[float],
) -> tuple[float, float, float, float]:
    """Fit weights via NNLS. Normalize to sum=1.0.

    Requires ML extras: pip install -e ".[ml]"

    Falls back to ``DEFAULT_WEIGHTS`` on any failure.
    """
    if not historical_scores:
        return DEFAULT_WEIGHTS
    if not _ML_AVAILABLE:
        import logging
        logging.getLogger(__name__).warning(
            "calibrate_weights skipped: ML dependencies missing. "
            "Install with: pip install -e \".[ml]\""
        )
        return DEFAULT_WEIGHTS

    try:
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
    except Exception:
        return DEFAULT_WEIGHTS
