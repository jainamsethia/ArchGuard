"""Git-history evolution must report failures, not a fabricated zero.

Reproduced live against six.git: every historical commit failed with "No
ArchGuard configuration found" -- the dashboard auto-generates .archguard.yml
into the working tree and never commits it, so a worktree checked out at an old
commit has no contract. The failures were swallowed and the endpoint returned
commits_analyzed=0 / debt_velocity=0.0 with error=None, which the UI rendered as
"Debt Velocity 0.0000" -- a failed run presented as a perfectly stable repo.
"""

from __future__ import annotations

from archguard.evolution.snapshots import (
    CommitAnalysisFailure,
    CommitHealthSnapshot,
    EvolutionReport,
)


def _snapshot(sha: str, health: float, when: str) -> CommitHealthSnapshot:
    return CommitHealthSnapshot(
        sha=sha,
        committed_at=when,
        health_score=health,
        composite_score=1.0 - health / 100.0,
        layer_scores={},
        violation_count=0,
        author="t",
        message="m",
    )


_NO_CONTRACT = "No ArchGuard configuration found."


# ---------------------------------------------------------------------------
# The report distinguishes "nothing to measure" from "measured nothing"
# ---------------------------------------------------------------------------


def test_total_failure_is_distinguishable_from_an_empty_repo():
    """Both yield zero snapshots; only one means the analysis failed."""
    failed = EvolutionReport(
        snapshots=[],
        failures=[CommitAnalysisFailure("a" * 40, _NO_CONTRACT)] * 3,
        commits_attempted=3,
    )
    nothing_to_do = EvolutionReport(snapshots=[], failures=[], commits_attempted=0)

    assert failed.all_failed is True
    assert nothing_to_do.all_failed is False


def test_failure_summary_names_the_dominant_cause():
    report = EvolutionReport(
        snapshots=[],
        failures=[
            CommitAnalysisFailure("a" * 40, _NO_CONTRACT),
            CommitAnalysisFailure("b" * 40, _NO_CONTRACT),
            CommitAnalysisFailure("c" * 40, "something else"),
        ],
        commits_attempted=3,
    )

    assert report.failure_summary == _NO_CONTRACT
    assert report.failure_count == 3


def test_partial_failure_is_not_treated_as_total_failure():
    """Some commits succeeded, so there are real numbers to report."""
    report = EvolutionReport(
        snapshots=[_snapshot("a" * 40, 90.0, "2026-01-01T00:00:00+00:00")],
        failures=[CommitAnalysisFailure("b" * 40, _NO_CONTRACT)],
        commits_attempted=2,
    )

    assert report.all_failed is False
    assert report.analysed_count == 1
    assert report.failure_count == 1


def test_a_clean_run_reports_no_failures():
    report = EvolutionReport(
        snapshots=[
            _snapshot("a" * 40, 90.0, "2026-01-01T00:00:00+00:00"),
            _snapshot("b" * 40, 80.0, "2026-01-02T00:00:00+00:00"),
        ],
        commits_attempted=2,
    )

    assert report.all_failed is False
    assert report.failure_count == 0
    assert report.failure_summary == ""


# ---------------------------------------------------------------------------
# The endpoint refuses to present an unmeasured run as a measurement
# ---------------------------------------------------------------------------


def _run_endpoint(monkeypatch, tmp_path, report):
    from archguard.dashboard.routes import evolution as ev

    monkeypatch.setattr(ev, "get_target_path", lambda jid: tmp_path, raising=False)
    monkeypatch.setattr(
        "archguard.dashboard.app.get_target_path", lambda jid: tmp_path, raising=False
    )

    class _FakeTracker:
        def __init__(self, path):
            pass

        def analyze_history(self, max_commits=10):
            return report

    monkeypatch.setattr(
        "archguard.evolution.tracker.ArchitectureEvolutionTracker", _FakeTracker
    )
    return ev.start_evolution(ev.EvolutionAnalyzeRequest(max_commits=3), job_id=None)


def test_endpoint_reports_the_real_cause_when_every_commit_failed(monkeypatch, tmp_path):
    report = EvolutionReport(
        snapshots=[],
        failures=[CommitAnalysisFailure("a" * 40, _NO_CONTRACT)] * 3,
        commits_attempted=3,
    )

    out = _run_endpoint(monkeypatch, tmp_path, report)

    assert out["error"] == "no_commits_analyzable"
    assert out["commits_attempted"] == 3
    assert out["commits_failed"] == 3
    assert _NO_CONTRACT in out["failure_reason"]
    # The specific regression: no zero-valued metrics that read as a real result.
    assert "debt_velocity" not in out
    assert out["snapshots"] == []


def test_endpoint_surfaces_partial_failures_alongside_real_results(
    monkeypatch, tmp_path
):
    report = EvolutionReport(
        snapshots=[
            _snapshot("a" * 40, 90.0, "2026-01-01T00:00:00+00:00"),
            _snapshot("b" * 40, 80.0, "2026-01-02T00:00:00+00:00"),
        ],
        failures=[CommitAnalysisFailure("c" * 40, _NO_CONTRACT)],
        commits_attempted=3,
    )

    out = _run_endpoint(monkeypatch, tmp_path, report)

    assert "error" not in out
    assert out["commits_analyzed"] == 2
    assert out["commits_failed"] == 1
    assert out["commits_attempted"] == 3
    assert _NO_CONTRACT in out["failure_reason"]
    assert out["debt_velocity"] is not None
