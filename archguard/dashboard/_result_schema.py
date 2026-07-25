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
