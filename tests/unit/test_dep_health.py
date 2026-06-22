"""Unit tests for Dependency Health Score (pip-audit integration)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

from archguard.analysis.deps import analyze_dependencies


def test_no_requirements_file(tmp_path: Path):
    """If no requirements file is found, it should be skipped."""
    res = analyze_dependencies(tmp_path)
    assert res.skipped is True
    assert "No requirements file found" in res.skip_reason


def test_pip_audit_not_found(tmp_path: Path):
    """Handle FileNotFoundError when pip-audit is missing."""
    (tmp_path / "requirements.txt").touch()

    with patch("subprocess.run", side_effect=FileNotFoundError):
        res = analyze_dependencies(tmp_path)
        assert res.skipped is True
        assert "pip-audit not found" in res.skip_reason


def test_pip_audit_timeout(tmp_path: Path):
    """Handle subprocess.TimeoutExpired gracefully."""
    (tmp_path / "pyproject.toml").touch()

    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="pip-audit", timeout=60),
    ):
        res = analyze_dependencies(tmp_path)
        assert res.skipped is True
        assert "timed out after 60 seconds" in res.skip_reason


def test_command_generation_pyproject_poetry(tmp_path: Path):
    """Poetry pyproject.toml should use environment scan (no path arg)."""
    (tmp_path / "pyproject.toml").write_text('[tool.poetry]\nname = "test"\n')

    with patch("subprocess.run") as mock_run:
        mock_process = MagicMock()
        mock_process.stdout = "{}"
        mock_process.stderr = ""
        mock_run.return_value = mock_process

        analyze_dependencies(tmp_path)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "-r" not in cmd
        assert str(tmp_path) not in cmd
        assert cmd == ["pip-audit", "--format=json"]


def test_command_generation_pyproject_pep621(tmp_path: Path):
    """PEP 621 pyproject.toml should use project path positional argument."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')

    with patch("subprocess.run") as mock_run:
        mock_process = MagicMock()
        mock_process.stdout = "{}"
        mock_process.stderr = ""
        mock_run.return_value = mock_process

        analyze_dependencies(tmp_path)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "-r" not in cmd
        assert cmd == ["pip-audit", "--format=json", str(tmp_path)]


def test_command_generation_requirements(tmp_path: Path):
    """requirements.txt should use -r flag."""
    (tmp_path / "requirements.txt").touch()

    with patch("subprocess.run") as mock_run:
        mock_process = MagicMock()
        mock_process.stdout = "{}"
        mock_process.stderr = ""
        mock_run.return_value = mock_process

        analyze_dependencies(tmp_path)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "-r" in cmd
        assert str(tmp_path / "requirements.txt") in cmd
        assert cmd == [
            "pip-audit",
            "--format=json",
            "-r",
            str(tmp_path / "requirements.txt"),
        ]


def test_vulnerability_parsing(tmp_path: Path):
    """Parse valid pip-audit JSON output (V2 format)."""
    (tmp_path / "requirements.txt").touch()

    # Mock output matching pip-audit JSON structure
    mock_output = {
        "dependencies": [
            {
                "name": "flask",
                "version": "1.0",
                "vulns": [
                    {"id": "CVE-2023-1234", "description": "A bad vulnerability."}
                ],
            },
            {"name": "requests", "version": "2.20.0", "vulns": []},
        ]
    }

    mock_process = MagicMock()
    mock_process.stdout = json.dumps(mock_output)
    mock_process.stderr = ""

    with patch("subprocess.run", return_value=mock_process):
        res = analyze_dependencies(tmp_path)

    assert res.skipped is False
    assert res.scanned_packages == 2
    assert len(res.vulnerabilities) == 1
    assert res.vulnerabilities[0].package == "flask"
    assert res.vulnerabilities[0].version == "1.0"
    assert res.vulnerabilities[0].vulnerability_id == "CVE-2023-1234"
    assert res.score == 90.0  # 1 vuln * 10 = 10, 100 - 10 = 90


def test_score_calculation(tmp_path: Path):
    """Score decreases by 10 per vulnerability."""
    (tmp_path / "requirements.txt").touch()

    mock_output = [
        {
            "name": "django",
            "version": "3.0",
            "vulns": [{"id": "CVE-1"}, {"id": "CVE-2"}, {"id": "CVE-3"}],
        }
    ]

    mock_process = MagicMock()
    mock_process.stdout = json.dumps(mock_output)
    mock_process.stderr = ""

    with patch("subprocess.run", return_value=mock_process):
        res = analyze_dependencies(tmp_path)

    assert res.skipped is False
    assert len(res.vulnerabilities) == 3
    assert res.score == 70.0  # 100 - 30


def test_score_floor(tmp_path: Path):
    """Score shouldn't go below 0."""
    (tmp_path / "requirements.txt").touch()

    mock_output = [
        {
            "name": "django",
            "version": "3.0",
            "vulns": [{"id": f"CVE-{i}"} for i in range(15)],
        }
    ]

    mock_process = MagicMock()
    mock_process.stdout = json.dumps(mock_output)
    mock_process.stderr = ""

    with patch("subprocess.run", return_value=mock_process):
        res = analyze_dependencies(tmp_path)

    assert res.skipped is False
    assert len(res.vulnerabilities) == 15
    assert res.score == 0.0  # 100 - 150 < 0, floor at 0
