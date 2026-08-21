from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from pydriller import Repository

from archguard.evolution.models import (
    EvolutionReport,
    EvolutionSnapshot,
    MetricTrend,
    TrendClassification,
)
from archguard.evolution.snapshots import (
    CommitAnalysisFailure,
    CommitHealthSnapshot,
)
from archguard.evolution.snapshots import (
    EvolutionReport as ArchEvolutionReport,
)
from archguard.evolution.worktree import git_worktree

logger = logging.getLogger(__name__)


class EvolutionTracker:
    """Tracks architecture metrics evolution over time."""

    def __init__(self, raw_snapshots: list[dict[str, Any]]) -> None:
        """Initialize with a list of raw dictionaries (e.g. from audit log)."""
        self.snapshots = self._parse_snapshots(raw_snapshots)

    def _parse_snapshots(self, raw_snapshots: list[dict[str, Any]]) -> list[EvolutionSnapshot]:
        parsed = []
        for raw in raw_snapshots:
            if "timestamp" not in raw:
                continue

            try:
                ts_str = raw["timestamp"].replace("Z", "+00:00")
                ts = datetime.fromisoformat(ts_str)
            except ValueError:
                continue

            score = float(raw.get("score", 0.0))
            debt_score = 1.0 - (score / 100.0)

            violations = raw.get("violations", [])
            v_count = len(violations)

            fitness_passed = None
            fitness_total = None
            metrics = raw.get("metrics", {})
            if "fitness_results" in metrics:
                fitness_results = metrics["fitness_results"]
                fitness_total = len(fitness_results)
                fitness_passed = sum(1 for fr in fitness_results if fr.get("passed", True))

            parsed.append(EvolutionSnapshot(
                timestamp=ts,
                health_score=score,
                debt_score=debt_score,
                violation_count=v_count,
                fitness_passed=fitness_passed,
                fitness_total=fitness_total
            ))

        # Sort chronologically (oldest first)
        return sorted(parsed, key=lambda s: s.timestamp)

    def generate_report(self) -> EvolutionReport:
        """Generate an EvolutionReport based on the loaded snapshots."""
        if not self.snapshots:
            return EvolutionReport(
                snapshots=[],
                health_trend=self._neutral_trend("health_score", 0.0),
                violation_trend=self._neutral_trend("violation_count", 0.0),
                debt_trend=self._neutral_trend("debt_score", 1.0),
                fitness_trend=None
            )

        current = self.snapshots[-1]
        previous = self.snapshots[-2] if len(self.snapshots) > 1 else None

        return EvolutionReport(
            snapshots=self.snapshots,
            health_trend=self._calc_trend("health_score", current.health_score, previous.health_score if previous else None, higher_is_better=True),
            violation_trend=self._calc_trend("violation_count", float(current.violation_count), float(previous.violation_count) if previous else None, higher_is_better=False),
            debt_trend=self._calc_trend("debt_score", current.debt_score, previous.debt_score if previous else None, higher_is_better=False),
            fitness_trend=self._calc_fitness_trend(current, previous)
        )

    def _neutral_trend(self, name: str, current: float) -> MetricTrend:
        return MetricTrend(
            name=name,
            current_value=current,
            previous_value=None,
            classification=TrendClassification.INSUFFICIENT,
            delta=None
        )

    def _calc_trend(self, name: str, current: float, previous: float | None, higher_is_better: bool) -> MetricTrend:
        if previous is None:
            return self._neutral_trend(name, current)

        delta = current - previous

        if abs(delta) < 0.0001:
            classification = TrendClassification.STABLE
        elif (delta > 0 and higher_is_better) or (delta < 0 and not higher_is_better):
            classification = TrendClassification.IMPROVING
        else:
            classification = TrendClassification.DECLINING

        return MetricTrend(
            name=name,
            current_value=current,
            previous_value=previous,
            classification=classification,
            delta=delta
        )

    def _calc_fitness_trend(self, current: EvolutionSnapshot, previous: EvolutionSnapshot | None) -> MetricTrend | None:
        if current.fitness_total is None or current.fitness_passed is None or current.fitness_total == 0:
            return None

        current_ratio = current.fitness_passed / current.fitness_total

        if previous is None or previous.fitness_total is None or previous.fitness_passed is None or previous.fitness_total == 0:
            return self._calc_trend("fitness_score", current_ratio, None, higher_is_better=True)

        previous_ratio = previous.fitness_passed / previous.fitness_total
        return self._calc_trend("fitness_score", current_ratio, previous_ratio, higher_is_better=True)


class ArchitectureEvolutionTracker:
    def __init__(self, repo_path: Path | str):
        self.repo_path = Path(repo_path).resolve()
        # sha -> why its analysis failed, populated by _analyze_commit and
        # drained by analyze_history. Worker threads only ever write their own
        # key, so a plain dict is safe here.
        self._failure_reasons: dict[str, str] = {}

    def analyze_history(self, max_commits: int = 10, max_workers: int = 4) -> ArchEvolutionReport:
        commits = list(Repository(str(self.repo_path)).traverse_commits())
        if len(commits) > max_commits:
            commits = commits[-max_commits:]
        snapshots = []
        failures: list[CommitAnalysisFailure] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_commit = {
                executor.submit(self._analyze_commit, commit.hash, commit.author_date.isoformat(), commit.author.name, commit.msg): commit.hash
                for commit in commits
            }
            for future in as_completed(future_to_commit):
                sha = future_to_commit[future]
                try:
                    snapshot = future.result()
                    if snapshot:
                        snapshots.append(snapshot)
                    else:
                        # _analyze_commit already logged the cause; it returns
                        # None rather than raising, so without recording it here
                        # the failure vanishes and the caller sees an empty
                        # result indistinguishable from a repo with no history.
                        failures.append(
                            CommitAnalysisFailure(
                                sha=sha,
                                reason=self._failure_reasons.pop(
                                    sha, "analysis failed for this commit"
                                ),
                            )
                        )
                except Exception as e:
                    logger.exception(f"Failed to analyze commit: {e}")
                    failures.append(CommitAnalysisFailure(sha=sha, reason=str(e)))
        snapshots.sort(key=lambda s: s.committed_at)
        return ArchEvolutionReport(
            snapshots=snapshots,
            failures=failures,
            commits_attempted=len(commits),
        )

    def _analyze_commit(self, sha: str, committed_at: str, author: str, message: str) -> CommitHealthSnapshot | None:
        from archguard.analysis.layers import AnalysisOrchestrator
        with git_worktree(self.repo_path, sha) as wt_path:
            try:
                with AnalysisOrchestrator(Path(wt_path)) as orchestrator:
                    all_py = list(Path(wt_path).rglob("*.py"))
                    res = orchestrator.run(all_py, sha, quiet=True)
                    return CommitHealthSnapshot(
                        sha=sha,
                        committed_at=committed_at,
                        health_score=res.archdebt.health_score,
                        composite_score=res.archdebt.composite_score,
                        layer_scores={
                            "layer1": res.layer_scores.layer1_violation,
                            "layer2": res.layer_scores.layer2_coupling,
                            "layer3": res.layer_scores.layer3_drift,
                            "layer4": res.layer_scores.layer4_duplication,
                        } if res.layer_scores else {},
                        violation_count=len(res.violations),
                        author=author,
                        message=message
                    )
            except Exception as e:
                logger.exception(f"Failed to analyze commit {sha}: {e}")
                # Recorded so analyze_history can report *why*, not merely that
                # the count was zero.
                self._failure_reasons[sha] = str(e)
                return None
