from __future__ import annotations
from dataclasses import dataclass, field


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
class CommitAnalysisFailure:
    """A commit that could not be analysed, and why."""

    sha: str
    reason: str


@dataclass
class EvolutionReport:
    snapshots: list[CommitHealthSnapshot]
    # Commits that were attempted but could not be analysed. Kept alongside the
    # successes so a run where everything failed is distinguishable from a run
    # over a repository with no history: both produce zero snapshots, but only
    # one of them means "we measured nothing and could not tell you why".
    failures: list[CommitAnalysisFailure] = field(default_factory=list)
    commits_attempted: int = 0

    @property
    def analysed_count(self) -> int:
        return len(self.snapshots)

    @property
    def failure_count(self) -> int:
        return len(self.failures)

    @property
    def all_failed(self) -> bool:
        """True when commits were attempted and none of them could be analysed."""
        return self.commits_attempted > 0 and not self.snapshots

    @property
    def failure_summary(self) -> str:
        """The most common failure cause, for reporting to a user."""
        if not self.failures:
            return ""
        counts: dict[str, int] = {}
        for f in self.failures:
            counts[f.reason] = counts.get(f.reason, 0) + 1
        return max(counts.items(), key=lambda kv: kv[1])[0]

    @property
    def debt_velocity(self) -> float:
        if len(self.snapshots) < 2:
            return 0.0
        recent = self.snapshots[-10:]
        if len(recent) < 2:
            return 0.0
        total_delta = 0.0
        for i in range(1, len(recent)):
            total_delta += recent[i - 1].health_score - recent[i].health_score
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
