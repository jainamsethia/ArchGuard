"""Integration tests for the HTML report command."""

import json
from pathlib import Path
from archguard.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()

def test_report_cmd_success(tmp_path):
    # Setup mock contract
    contract = {
        "version": "3.0",
        "modules": [
            {"name": "test_mod", "path": "src/"}
        ],
        "fail_threshold": 0.75,
        "warn_threshold": 0.50
    }
    contract_file = tmp_path / ".archguard.yml"
    with open(contract_file, "w") as f:
        import yaml
        yaml.dump(contract, f)

    # Setup dummy python file
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "foo.py").write_text("import os\n")

    # Run command
    output_html = tmp_path / "report.html"
    result = runner.invoke(app, ["report", "--root", str(tmp_path), "--output", str(output_html)])

    assert result.exit_code == 0
    assert output_html.exists()

    html_content = output_html.read_text("utf-8")
    assert "<title>ArchGuard Health Report</title>" in html_content
    assert "vis-network.min.js" in html_content
    assert "chart.js" in html_content
    assert "const SUMMARY =" in html_content
    assert "const VIOLATIONS =" in html_content
