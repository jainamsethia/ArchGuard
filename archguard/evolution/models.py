from __future__ import annotations
from enum import Enum
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel


class TrendClassification(str, Enum):
    IMPROVING = "improving"
    STABLE = "stable"
    DECLINING = "declining"
    INSUFFICIENT = "insufficient"


class MetricTrend(BaseModel):
    name: str
    current_value: float
    previous_value: Optional[float]
    classification: TrendClassification
    delta: Optional[float]


class EvolutionSnapshot(BaseModel):
    timestamp: datetime
    health_score: float
    debt_score: float
    violation_count: int
    fitness_passed: Optional[int] = None
    fitness_total: Optional[int] = None


class EvolutionReport(BaseModel):
    snapshots: List[EvolutionSnapshot]
    health_trend: MetricTrend
    violation_trend: MetricTrend
    debt_trend: MetricTrend
    fitness_trend: Optional[MetricTrend] = None
