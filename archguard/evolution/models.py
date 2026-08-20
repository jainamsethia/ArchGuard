from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class TrendClassification(str, Enum):
    IMPROVING = "improving"
    STABLE = "stable"
    DECLINING = "declining"
    INSUFFICIENT = "insufficient"


class MetricTrend(BaseModel):
    name: str
    current_value: float
    previous_value: float | None
    classification: TrendClassification
    delta: float | None


class EvolutionSnapshot(BaseModel):
    timestamp: datetime
    health_score: float
    debt_score: float
    violation_count: int
    fitness_passed: int | None = None
    fitness_total: int | None = None


class EvolutionReport(BaseModel):
    snapshots: list[EvolutionSnapshot]
    health_trend: MetricTrend
    violation_trend: MetricTrend
    debt_trend: MetricTrend
    fitness_trend: MetricTrend | None = None
