"""Single source of truth for analysis-result JSON shape, used by both
pipeline_adapter.py (live tracking) and audit/logger.py (persisted history)."""
from pydantic import BaseModel, Field

class ViolationPayload(BaseModel):
    file: str | None = None
    module: str | None = None
    severity: str
    message: str
    layer: str

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
