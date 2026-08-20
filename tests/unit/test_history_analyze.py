"""Unit tests for the history-analyze CLI command (Phase 3 Step 9)."""

import json

import pytest

from archguard.cli.history_analyze_cmd import (
    _build_json_output,
    _calc_debt_velocity,
    _get_commit_shas,
    _sparkline,
)

# ---------------------------------------------------------------------------
# _get_commit_shas
# ---------------------------------------------------------------------------


def test_get_commit_shas_returns_oldest_first(tmp_path):
    """Verify commit SHAs are returned oldest-first."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
        check=True,
        capture_output=True,
    )

    # Create 3 commits
    for i in range(3):
        (repo / f"file{i}.txt").write_text(f"v{i}")
        subprocess.run(
            ["git", "-C", str(repo), "add", "."], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", f"commit {i}"],
            check=True,
            capture_output=True,
        )

    shas = _get_commit_shas(repo, max_commits=10)
    assert len(shas) == 3
    # Oldest commit should be first
    assert shas[0] != shas[-1]

    # Verify ordering: get log in chronological order to compare
    result = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%H", "--reverse"],
        capture_output=True,
        text=True,
        check=True,
    )
    expected = [s.strip() for s in result.stdout.strip().splitlines()]
    assert shas == expected


def test_get_commit_shas_respects_max_commits(tmp_path):
    """Verify max_commits limits the number of returned SHAs."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
        check=True,
        capture_output=True,
    )

    for i in range(5):
        (repo / f"file{i}.txt").write_text(f"v{i}")
        subprocess.run(
            ["git", "-C", str(repo), "add", "."], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", f"commit {i}"],
            check=True,
            capture_output=True,
        )

    shas = _get_commit_shas(repo, max_commits=3)
    assert len(shas) == 3


def test_get_commit_shas_empty_repo(tmp_path):
    """Non-git directory returns empty list."""
    # Create an isolated dir with its own .git-less structure to avoid
    # git -C falling back to a parent repo.
    isolated = tmp_path / "not_a_repo"
    isolated.mkdir()
    (isolated / "dummy.txt").write_text("x")
    # Init a repo but with zero commits
    import subprocess

    subprocess.run(
        ["git", "-C", str(isolated), "init"], check=True, capture_output=True
    )
    shas = _get_commit_shas(isolated, max_commits=5)
    assert shas == []


# ---------------------------------------------------------------------------
# _calc_debt_velocity
# ---------------------------------------------------------------------------


def test_debt_velocity_improving():
    """Health going up -> debt going down -> negative velocity."""
    snapshots = [
        {"score": 70.0},
        {"score": 80.0},
        {"score": 90.0},
    ]
    vel = _calc_debt_velocity(snapshots)
    assert vel is not None
    assert vel < 0  # debt is decreasing


def test_debt_velocity_declining():
    """Health going down -> debt going up -> positive velocity."""
    snapshots = [
        {"score": 90.0},
        {"score": 80.0},
        {"score": 70.0},
    ]
    vel = _calc_debt_velocity(snapshots)
    assert vel is not None
    assert vel > 0  # debt is increasing


def test_debt_velocity_stable():
    """No change -> zero velocity."""
    snapshots = [
        {"score": 80.0},
        {"score": 80.0},
    ]
    vel = _calc_debt_velocity(snapshots)
    assert vel is not None
    assert vel == pytest.approx(0.0)


def test_debt_velocity_single_snapshot():
    """Single snapshot -> None (insufficient data)."""
    vel = _calc_debt_velocity([{"score": 80.0}])
    assert vel is None


def test_debt_velocity_empty():
    """Empty list -> None."""
    vel = _calc_debt_velocity([])
    assert vel is None


# ---------------------------------------------------------------------------
# _sparkline
# ---------------------------------------------------------------------------


def test_sparkline_basic():
    result = _sparkline([0, 50, 100])
    assert len(result) == 3
    assert result[0] == "▁"
    assert result[-1] == "█"


def test_sparkline_empty():
    assert _sparkline([]) == ""


def test_sparkline_uniform():
    result = _sparkline([50, 50, 50])
    assert len(result) == 3


# ---------------------------------------------------------------------------
# _build_json_output
# ---------------------------------------------------------------------------


def test_build_json_output_structure():
    """Verify the JSON output contains all required fields."""
    from archguard.evolution.tracker import EvolutionTracker

    snapshots = [
        {
            "timestamp": "2023-01-01T10:00:00Z",
            "score": 70.0,
            "violations": [{"id": 1}],
            "metrics": {},
            "commit_sha": "aaa1111",
        },
        {
            "timestamp": "2023-01-02T10:00:00Z",
            "score": 85.0,
            "violations": [],
            "metrics": {},
            "commit_sha": "bbb2222",
        },
    ]
    tracker = EvolutionTracker(snapshots)
    report = tracker.generate_report()
    velocity = _calc_debt_velocity(snapshots)

    output = _build_json_output(snapshots, report, velocity)

    assert output["commits_analyzed"] == 2
    assert "score_range" in output
    assert output["score_range"]["min"] == 70.0
    assert output["score_range"]["max"] == 85.0
    assert output["debt_velocity"] is not None
    assert output["debt_velocity"] < 0  # improving
    assert "trends" in output
    assert "health" in output["trends"]
    assert "violations" in output["trends"]
    assert "debt" in output["trends"]
    assert output["trends"]["health"]["classification"] == "improving"
    assert len(output["snapshots"]) == 2


def test_build_json_output_empty_history():
    """Verify JSON output handles empty history gracefully."""
    from archguard.evolution.tracker import EvolutionTracker

    tracker = EvolutionTracker([])
    report = tracker.generate_report()

    output = _build_json_output([], report, None)

    assert output["commits_analyzed"] == 0
    assert output["debt_velocity"] is None
    assert output["score_range"]["min"] is None
    assert output["score_range"]["max"] is None


def test_build_json_output_with_fitness():
    """Verify fitness trend is included when fitness data exists."""
    from archguard.evolution.tracker import EvolutionTracker

    snapshots = [
        {
            "timestamp": "2023-01-01T10:00:00Z",
            "score": 70.0,
            "violations": [],
            "metrics": {"fitness_results": [{"passed": False}, {"passed": False}]},
            "commit_sha": "aaa1111",
        },
        {
            "timestamp": "2023-01-02T10:00:00Z",
            "score": 85.0,
            "violations": [],
            "metrics": {"fitness_results": [{"passed": True}, {"passed": True}]},
            "commit_sha": "bbb2222",
        },
    ]
    tracker = EvolutionTracker(snapshots)
    report = tracker.generate_report()
    velocity = _calc_debt_velocity(snapshots)

    output = _build_json_output(snapshots, report, velocity)
    assert "fitness" in output["trends"]
    assert output["trends"]["fitness"]["classification"] == "improving"


def test_build_json_is_serializable():
    """Verify the output is valid JSON."""
    from archguard.evolution.tracker import EvolutionTracker

    snapshots = [
        {
            "timestamp": "2023-01-01T10:00:00Z",
            "score": 75.0,
            "violations": [],
            "metrics": {},
            "commit_sha": "ccc3333",
        },
    ]
    tracker = EvolutionTracker(snapshots)
    report = tracker.generate_report()

    output = _build_json_output(snapshots, report, None)
    serialized = json.dumps(output, indent=2)
    parsed = json.loads(serialized)
    assert parsed["commits_analyzed"] == 1
