"""Deterministic ordering and selection of findings for LLM remediation.

Why this exists: the remediation prompt used to take ``violations[:15]`` -- the
first fifteen in whatever order the layers happened to append them. Two runs of
the same analysis could send a different fifteen, and a LOW-severity cosmetic
finding could crowd out a CRITICAL one.

What this deliberately does **not** do is compute a blended "priority score"
from severity, magnitude and impact. Every number here is one the analysis
already measured for its own purposes; nothing is weighted against anything of a
different type. That pattern -- a precise-looking number assembled from
incommensurable parts -- is the same one already removed twice from this
codebase (the fixed 0.75 composite floor, the self-referential coupling budget),
and it is exactly what makes output look authoritative while being arbitrary.

ArchGuard decides what is a violation and how severe it is. This module only
decides what order to show them in, and which ones fit under the LLM cap.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from archguard.analysis import violation_kinds

# Severity tiers, worst first. A failed *critical* fitness gate (currently the
# dependency-cycle check) sits above ordinary CRITICAL findings: it already caps
# the reported health grade in scoring.apply_fitness_results, so treating it as
# the top tier here keeps the two consistent.
FITNESS_GATE_TIER: int = 0
SEVERITY_TIERS: dict[str, int] = {
    "critical": 1,
    "high": 2,
    "medium": 3,
    "low": 4,
}
_UNKNOWN_SEVERITY_TIER: int = 5

# Default number of findings eligible for an LLM remediation plan.
DEFAULT_REMEDIATION_LIMIT: int = 15


@dataclass(frozen=True)
class RankedFinding:
    """A violation (or failed gate) with its position in the ordering."""

    finding: dict[str, Any]
    tier: int
    kind: str
    metric: float
    is_fitness_gate: bool = False

    @property
    def sort_key(self) -> tuple[Any, ...]:
        """Total order over findings.

        ``kind`` precedes ``metric`` on purpose. Within one severity tier two
        different kinds can coexist, and their metrics are not the same quantity
        -- 0.30 "fraction over fan-out budget" and 0.30 "duplication score"
        share a scale only by coincidence. Grouping by kind first means each
        metric is only ever compared against others of its own type.

        The trailing module/file/message terms are the explicit final
        tiebreaker: without them, two findings identical in tier, kind and
        metric would keep whatever order the layer happened to emit, and
        "deterministic" would hold only until two modules tied.
        """
        return (
            self.tier,
            self.kind,
            -self.metric,
            str(self.finding.get("module") or ""),
            str(self.finding.get("file") or ""),
            str(self.finding.get("message") or ""),
        )


@dataclass
class Selection:
    """Outcome of ranking: what was found, dropped, and sent to the LLM."""

    selected: list[RankedFinding] = field(default_factory=list)
    detected_count: int = 0
    suppressed_count: int = 0
    eligible_count: int = 0
    limit: int = DEFAULT_REMEDIATION_LIMIT

    @property
    def selected_count(self) -> int:
        return len(self.selected)


def finding_key(finding: dict[str, Any]) -> str:
    """Stable identity for a finding, for matching a plan back to a table row.

    Uses the same (module, layer, message) triple the suppression store hashes,
    so a row identified here is the same row a user can suppress.
    """
    return "|".join(
        (
            str(finding.get("module") or ""),
            str(finding.get("layer") or ""),
            str(finding.get("message") or ""),
        )
    )


def natural_metric(kind: str, metrics: dict[str, float]) -> float:
    """The magnitude a kind is ordered by, in that kind's own terms.

    Each is a quantity the analysis already computed to raise the violation --
    none is invented here for ranking:

    * fan-out    -> how far past its budget the module is, as a fraction. This
      is the same quantity Layer 2 uses as its coupling delta.
    * duplication-> the aggregate duplication score Layer 4 compared against the
      module's threshold.
    * drift      -> the cosine distance Layer 3 compared against its threshold.

    Kinds with no magnitude (a forbidden import either happened or it didn't)
    return 0.0 and fall through to the alphabetical tiebreaker.
    """
    if kind == violation_kinds.FAN_OUT:
        return violation_kinds.over_budget_ratio(metrics)
    if kind == violation_kinds.DUPLICATION:
        return float(metrics.get("duplication_score", 0.0))
    if kind == violation_kinds.SEMANTIC_DRIFT:
        return float(metrics.get("drift", 0.0))
    return 0.0


def _severity_tier(finding: dict[str, Any]) -> int:
    severity = str(finding.get("severity", "") or "").lower()
    return SEVERITY_TIERS.get(severity, _UNKNOWN_SEVERITY_TIER)


def rank_violations(violations: Iterable[dict[str, Any]]) -> list[RankedFinding]:
    """Order violations deterministically. Does not filter or truncate."""
    ranked = [
        RankedFinding(
            finding=v,
            tier=_severity_tier(v),
            kind=str(v.get("kind") or ""),
            metric=natural_metric(
                str(v.get("kind") or ""), dict(v.get("metrics") or {})
            ),
        )
        for v in violations
    ]
    return sorted(ranked, key=lambda r: r.sort_key)


def failed_critical_gates(
    fitness_results: Iterable[dict[str, Any]],
) -> list[RankedFinding]:
    """Failed critical fitness gates, as top-tier findings."""
    out: list[RankedFinding] = []
    for fr in fitness_results:
        if fr.get("passed", True):
            continue
        if str(fr.get("severity", "")).lower() != "critical":
            continue
        out.append(
            RankedFinding(
                finding={
                    "module": "",
                    "file": "",
                    "message": str(fr.get("evidence") or fr.get("name") or ""),
                    "severity": "critical",
                    "kind": violation_kinds.DEPENDENCY_CYCLE,
                    "name": fr.get("name") or fr.get("rule") or "",
                    "metrics": {},
                },
                tier=FITNESS_GATE_TIER,
                kind=violation_kinds.DEPENDENCY_CYCLE,
                metric=0.0,
                is_fitness_gate=True,
            )
        )
    return sorted(out, key=lambda r: r.sort_key)


def select_for_remediation(
    violations: Iterable[dict[str, Any]],
    fitness_results: Iterable[dict[str, Any]] = (),
    limit: int = DEFAULT_REMEDIATION_LIMIT,
    is_suppressed: Callable[[dict[str, Any]], bool] | None = None,
) -> Selection:
    """Rank findings and take the top *limit* for an LLM remediation plan.

    Suppressed violations are dropped before selection, not after: the user has
    already dismissed them, and letting them occupy slots would push real
    findings out of the capped set.
    """
    all_violations = list(violations)
    suppressed_count = 0
    eligible: list[dict[str, Any]] = []
    for v in all_violations:
        if is_suppressed is not None and is_suppressed(v):
            suppressed_count += 1
            continue
        eligible.append(v)

    ordered = failed_critical_gates(fitness_results) + rank_violations(eligible)

    return Selection(
        selected=ordered[:limit],
        detected_count=len(all_violations),
        suppressed_count=suppressed_count,
        eligible_count=len(eligible),
        limit=limit,
    )
