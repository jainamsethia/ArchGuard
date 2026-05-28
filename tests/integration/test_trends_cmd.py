"""Integration tests for the trends command."""

import json
from datetime import datetime, timezone, timedelta
from archguard.cli.main import app
from typer.testing import CliRunner
from archguard.config import AUDIT_LOG_FILENAME

runner = CliRunner()

def test_trends_cmd_success(tmp_path, monkeypatch):
    # Mock the audit log path
    mock_log = tmp_path / AUDIT_LOG_FILENAME
    mock_log.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("archguard.cli.trends_cmd.AUDIT_LOG_FILENAME", str(mock_log))

    # Create mock runs
    now = datetime.now(timezone.utc)
    runs = [
        {"timestamp": (now - timedelta(days=5)).isoformat(), "event": "analysis_run", "score": 70.0, "grade": "C", "violation_count": 10},
        {"timestamp": (now - timedelta(days=4)).isoformat(), "event": "analysis_run", "score": 75.0, "grade": "C+", "violation_count": 8},
        {"timestamp": (now - timedelta(days=3)).isoformat(), "event": "analysis_run", "score": 79.5, "grade": "C+", "violation_count": 7},
        {"timestamp": (now - timedelta(days=2)).isoformat(), "event": "analysis_run", "score": 82.0, "grade": "B-", "violation_count": 5},
        {"timestamp": (now - timedelta(days=1)).isoformat(), "event": "analysis_run", "score": 87.5, "grade": "B", "violation_count": 3},
    ]

    with open(mock_log, "w", encoding="utf-8") as f:
        for run in runs:
            f.write(json.dumps(run) + "\n")

    result = runner.invoke(app, ["trends"])
    assert result.exit_code == 0
    assert "87.5" in result.stdout
    assert "79.5" in result.stdout
    assert "Trend: ↑ +17.5 points over 5 runs (improving)" in result.stdout
    assert "Score history:" in result.stdout

def test_trends_cmd_json(tmp_path, monkeypatch):
    mock_log = tmp_path / AUDIT_LOG_FILENAME
    mock_log.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("archguard.cli.trends_cmd.AUDIT_LOG_FILENAME", str(mock_log))

    now = datetime.now(timezone.utc)
    runs = [
        {"timestamp": (now - timedelta(days=1)).isoformat(), "event": "analysis_run", "score": 87.5, "grade": "B", "violation_count": 3},
    ]
    with open(mock_log, "w", encoding="utf-8") as f:
        for run in runs:
            f.write(json.dumps(run) + "\n")

    result = runner.invoke(app, ["trends", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data["runs"]) == 1
    assert data["runs"][0]["score"] == 87.5
