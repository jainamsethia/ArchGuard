from unittest.mock import patch

from typer.testing import CliRunner

from archguard.cli.main import app

runner = CliRunner()


@patch("archguard.cli.history_analyze_cmd._get_commit_shas")
@patch("archguard.cli.history_analyze_cmd._analyze_commit")
def test_history_analyze_no_commits(mock_analyze, mock_get_shas, tmp_path):
    mock_get_shas.return_value = []

    result = runner.invoke(app, ["history-analyze", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "No commits found in repository" in result.stdout


@patch("archguard.cli.history_analyze_cmd._get_commit_shas")
@patch("archguard.cli.history_analyze_cmd._analyze_commit")
def test_history_analyze_all_fail(mock_analyze, mock_get_shas, tmp_path):
    mock_get_shas.return_value = ["sha1", "sha2"]
    mock_analyze.return_value = None

    result = runner.invoke(app, ["history-analyze", "--repo", str(tmp_path)])
    assert result.exit_code == 1
    assert "All commit analyses failed" in result.stdout


@patch("archguard.cli.history_analyze_cmd._get_commit_shas")
@patch("archguard.cli.history_analyze_cmd._analyze_commit")
def test_history_analyze_success(mock_analyze, mock_get_shas, tmp_path):
    mock_get_shas.return_value = ["sha1", "sha2"]

    def side_effect(repo, sha):
        return {
            "commit_sha": sha,
            "score": 85.0 if sha == "sha2" else 75.0,
            "violations": [],
            "metrics": {}
        }

    mock_analyze.side_effect = side_effect

    result = runner.invoke(app, ["history-analyze", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "Architecture Evolution Report" in result.stdout
    assert "Score Range:" in result.stdout


@patch("archguard.cli.history_analyze_cmd._get_commit_shas")
@patch("archguard.cli.history_analyze_cmd._analyze_commit")
def test_history_analyze_json_success(mock_analyze, mock_get_shas, tmp_path):
    mock_get_shas.return_value = ["sha1", "sha2"]

    def side_effect(repo, sha):
        return {
            "commit_sha": sha,
            "score": 85.0 if sha == "sha2" else 75.0,
            "violations": [{"layer": 1, "message": "msg"}],
            "metrics": {}
        }

    mock_analyze.side_effect = side_effect

    result = runner.invoke(app, ["history-analyze", "--repo", str(tmp_path), "--json"])
    assert result.exit_code == 0

    import json
    data = json.loads(result.stdout)
    assert data["commits_analyzed"] == 2
    assert data["score_range"]["min"] == 75.0
    assert data["score_range"]["max"] == 85.0
    assert "trends" in data


@patch("archguard.cli.history_analyze_cmd._get_commit_shas")
@patch("archguard.cli.history_analyze_cmd._analyze_commit")
def test_history_analyze_json_no_commits(mock_analyze, mock_get_shas, tmp_path):
    mock_get_shas.return_value = []

    result = runner.invoke(app, ["history-analyze", "--repo", str(tmp_path), "--json"])
    assert result.exit_code == 0
    import json
    data = json.loads(result.stdout)
    assert "error" in data
    assert data["commits_analyzed"] == 0
