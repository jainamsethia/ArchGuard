"""Single source of truth for analysis-result JSON shape, used by both
pipeline_adapter.py (live tracking) and audit/logger.py (persisted history)."""
from typing import Any

from pydantic import BaseModel, Field


class ViolationPayload(BaseModel):
    file: str | None = None
    line: int | None = 0
    module: str | None = None
    severity: str
    message: str
    layer: str
    scope: str = "file"

    # Structured form of the facts already in `message`. Additive: runs
    # persisted before these existed simply carry the defaults, and the
    # plain-language renderer falls back to a generic template for them.
    kind: str = ""
    metrics: dict[str, float] = Field(default_factory=dict)

class LayerResultPayload(BaseModel):
    layer: int
    name: str
    score: float
    violation_count: int
    skipped: bool = False
    skip_reason: str = ""

class AnalysisResultPayload(BaseModel):
    job_id: str
    score: float | None
    band: str | None
    violations: list[ViolationPayload]
    skipped: bool = False
    layer_results: list[LayerResultPayload] = Field(default_factory=list)
    module_scores: dict[str, float] = Field(default_factory=dict)
    modules_analyzed: list[str] = Field(default_factory=list)
    dependency_graph: dict[str, list[str]] = Field(default_factory=dict)
    import_edges: list[dict[str, str]] = Field(default_factory=list)
    contract: dict[str, Any] = Field(default_factory=dict)

    # AnalysisResult.metrics, including "fitness_results". dashboard.js reads
    # latestRun.metrics.fitness_results to render the Fitness Functions panel,
    # so omitting this left that panel permanently empty and dropped fitness
    # outcomes from the audit trail even though they had been evaluated.
    metrics: dict[str, Any] = Field(default_factory=dict)

    # Provenance of the module map every score below is computed against.
    # These are additive; existing consumers that ignore them are unaffected.
    # When fallback_directory_heuristic is True the module boundaries were
    # guessed from top-level directory names, not measured from co-change
    # history, and the score/violations must be read with that caveat.
    contract_auto_generated: bool = False
    fallback_directory_heuristic: bool = False
    fallback_reason: str = ""

    # Set when the contract could be analysed but the derived artifacts
    # (module_scores / modules_analyzed / dependency_graph) could not be built.
    # Without it, an empty module list reads as "this repo has no modules"
    # rather than "ArchGuard failed to work them out".
    derived_artifacts_error: str = ""
