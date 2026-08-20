import json
from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from archguard.cli.main import app
from archguard.config import AUDIT_LOG_FILENAME

runner = CliRunner()


def test_history_cmd_empty(tmp_path):
    mock_log = tmp_path / AUDIT_LOG_FILENAME

    result = runner.invoke(app, ["history", "--audit-log", str(mock_log)])
    assert result.exit_code == 0
    assert "No audit history found" in result.stdout


def test_history_cmd_format_trend_one_run(tmp_path):
    mock_log = tmp_path / AUDIT_LOG_FILENAME
    mock_log.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC)
    runs = [
        {
            "timestamp": now.isoformat(),
            "event": "analysis_run",
            "score": 70.0,
            "grade": "C",
            "violation_count": 10,
        }
    ]
    with open(mock_log, "w", encoding="utf-8") as f:
        for run in runs:
            f.write(json.dumps(run) + "\n")

    result = runner.invoke(app, ["history", "--format", "trend", "--audit-log", str(mock_log)])
    assert result.exit_code == 0
    assert "Insufficient data for trend line" in result.stdout


def test_history_cmd_table_view(tmp_path):
    mock_log = tmp_path / AUDIT_LOG_FILENAME
    mock_log.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC)
    runs = [
        {
            "timestamp": (now - timedelta(days=1)).isoformat(),
            "event": "analysis_run",
            "score": 70.0,
            "grade": "C",
            "violation_count": 10,
            "pr_number": "123",
        },
        {
            "timestamp": now.isoformat(),
            "event": "analysis_run",
            "score": 80.0,
            "grade": "B",
            "violation_count": 5,
        }
    ]
    with open(mock_log, "w", encoding="utf-8") as f:
        for run in runs:
            f.write(json.dumps(run) + "\n")

    result = runner.invoke(app, ["history", "--format", "table", "--audit-log", str(mock_log)])
    assert result.exit_code == 0
    assert "70.0" in result.stdout
    assert "80.0" in result.stdout
    assert "123" in result.stdout
