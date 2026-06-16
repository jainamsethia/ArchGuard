from __future__ import annotations
from dataclasses import dataclass

@dataclass
class CommitHealthSnapshot:
    sha: str
    committed_at: str
    health_score: float
    composite_score: float
    layer_scores: dict[str, float]
    violation_count: int
    author: str
    message: str

@dataclass
class EvolutionReport:
    snapshots: list[CommitHealthSnapshot]

    @property
    def debt_velocity(self) -> float:
        if len(self.snapshots) < 2:
            return 0.0
        recent = self.snapshots[-10:]
        if len(recent) < 2:
            return 0.0
        total_delta = 0.0
        for i in range(1, len(recent)):
            total_delta += recent[i-1].health_score - recent[i].health_score
        return total_delta / (len(recent) - 1)

    @property
    def trend_direction(self) -> str:
        vel = self.debt_velocity
        if vel < -0.5:
            return "improving"
        elif vel > 0.5:
            return "declining"
        return "stable"

    @property
    def score_range(self) -> tuple[float, float]:
        if not self.snapshots:
            return (0.0, 0.0)
        scores = [s.health_score for s in self.snapshots]
        return (min(scores), max(scores))
