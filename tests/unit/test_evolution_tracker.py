import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from archguard.evolution.snapshots import CommitHealthSnapshot, EvolutionReport
from archguard.evolution.tracker import ArchitectureEvolutionTracker
from archguard.evolution.worktree import git_worktree

def test_debt_velocity_positive():
    snaps = [
        CommitHealthSnapshot(sha="1", committed_at="2026-01-01", health_score=76.0, composite_score=0.0, layer_scores={}, violation_count=0, author="", message=""),
        CommitHealthSnapshot(sha="2", committed_at="2026-01-02", health_score=72.0, composite_score=0.0, layer_scores={}, violation_count=0, author="", message=""),
        CommitHealthSnapshot(sha="3", committed_at="2026-01-03", health_score=70.0, composite_score=0.0, layer_scores={}, violation_count=0, author="", message=""),
    ]
    report = EvolutionReport(snapshots=snaps)
    assert report.debt_velocity == 3.0

def test_debt_velocity_negative():
    snaps = [
        CommitHealthSnapshot(sha="1", committed_at="2026-01-01", health_score=75.0, composite_score=0.0, layer_scores={}, violation_count=0, author="", message=""),
        CommitHealthSnapshot(sha="2", committed_at="2026-01-02", health_score=80.0, composite_score=0.0, layer_scores={}, violation_count=0, author="", message=""),
    ]
    report = EvolutionReport(snapshots=snaps)
    assert report.debt_velocity == -5.0

def test_debt_velocity_insufficient_data():
    snaps = [
        CommitHealthSnapshot(sha="1", committed_at="2026-01-01", health_score=80.0, composite_score=0.0, layer_scores={}, violation_count=0, author="", message=""),
    ]
    report = EvolutionReport(snapshots=snaps)
    assert report.debt_velocity == 0.0

def test_trend_direction_improving():
    snaps = [
        CommitHealthSnapshot(sha="1", committed_at="2026-01-01", health_score=70.0, composite_score=0.0, layer_scores={}, violation_count=0, author="", message=""),
        CommitHealthSnapshot(sha="2", committed_at="2026-01-02", health_score=72.0, composite_score=0.0, layer_scores={}, violation_count=0, author="", message=""),
    ]
    report = EvolutionReport(snapshots=snaps)
    assert report.trend_direction == "improving"

def test_trend_direction_declining():
    snaps = [
        CommitHealthSnapshot(sha="1", committed_at="2026-01-01", health_score=70.0, composite_score=0.0, layer_scores={}, violation_count=0, author="", message=""),
        CommitHealthSnapshot(sha="2", committed_at="2026-01-02", health_score=60.0, composite_score=0.0, layer_scores={}, violation_count=0, author="", message=""),
    ]
    report = EvolutionReport(snapshots=snaps)
    assert report.trend_direction == "declining"

def test_trend_direction_stable():
    snaps = [
        CommitHealthSnapshot(sha="1", committed_at="2026-01-01", health_score=70.0, composite_score=0.0, layer_scores={}, violation_count=0, author="", message=""),
        CommitHealthSnapshot(sha="2", committed_at="2026-01-02", health_score=70.1, composite_score=0.0, layer_scores={}, violation_count=0, author="", message=""),
    ]
    report = EvolutionReport(snapshots=snaps)
    assert report.trend_direction == "stable"

def test_score_range():
    snaps = [
        CommitHealthSnapshot(sha="1", committed_at="2026-01-01", health_score=50.0, composite_score=0.0, layer_scores={}, violation_count=0, author="", message=""),
        CommitHealthSnapshot(sha="2", committed_at="2026-01-02", health_score=90.0, composite_score=0.0, layer_scores={}, violation_count=0, author="", message=""),
        CommitHealthSnapshot(sha="3", committed_at="2026-01-03", health_score=70.0, composite_score=0.0, layer_scores={}, violation_count=0, author="", message=""),
    ]
    report = EvolutionReport(snapshots=snaps)
    assert report.score_range == (50.0, 90.0)

@patch("archguard.evolution.tracker.Repository")
@patch("archguard.evolution.tracker.git_worktree")
def test_analyze_history_empty_commits(mock_git_wt, mock_repo):
    mock_repo_instance = MagicMock()
    mock_repo_instance.traverse_commits.return_value = []
    mock_repo.return_value = mock_repo_instance
    
    tracker = ArchitectureEvolutionTracker(Path("."))
    report = tracker.analyze_history()
    
    assert len(report.snapshots) == 0
    assert report.score_range == (0.0, 0.0)
    assert report.debt_velocity == 0.0
    assert report.trend_direction == "stable"
