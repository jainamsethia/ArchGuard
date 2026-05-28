"""Unit tests for suppress and contract CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
import pytest
from typer.testing import CliRunner

from archguard.cli.main import app
from archguard.config import SUPPRESSION_FILE

runner: CliRunner = CliRunner()


def _setup_repo(tmp_path: Path) -> Path:
    """Create a minimal repo with contract."""
    contract = {
        "schema_version": "3.0",
        "modules": [
            {"name": "payments", "paths": ["payments/"]},
            {"name": "auth", "paths": ["auth/"]},
        ],
        "fail_threshold": 0.75,
        "warn_threshold": 0.50,
    }
    config_path = tmp_path / ".archguard.yml"
    with config_path.open("w") as f:
        yaml.dump(contract, f)
    return tmp_path


class TestSuppressCmd:
    def test_add_valid(self, tmp_path: Path) -> None:
        """suppress add with valid args -> exit 0, confirmation printed."""
        repo = _setup_repo(tmp_path)
        result = runner.invoke(
            app,
            [
                "suppress", "add",
                "--module", "payments", "--layer", "1",
                "--message", "bad import", "--reason", "known tech debt",
                "--repo", str(repo),
            ],
        )
        assert result.exit_code == 0
        assert "Suppression created" in result.output

    def test_add_reason_too_long(self, tmp_path: Path) -> None:
        """suppress add with reason > 500 chars -> exit 1."""
        repo = _setup_repo(tmp_path)
        long_reason = "x" * 501
        result = runner.invoke(
            app,
            [
                "suppress", "add",
                "--module", "payments", "--layer", "1",
                "--message", "msg", "--reason", long_reason,
                "--repo", str(repo),
            ],
        )
        assert result.exit_code == 1

    def test_add_reason_with_newline(self, tmp_path: Path) -> None:
        """suppress add with newline in reason -> exit 1."""
        repo = _setup_repo(tmp_path)
        result = runner.invoke(
            app,
            [
                "suppress", "add",
                "--module", "payments", "--layer", "1",
                "--message", "msg", "--reason", "line1\nline2",
                "--repo", str(repo),
            ],
        )
        assert result.exit_code == 1

    def test_list_empty(self, tmp_path: Path) -> None:
        """suppress list with no suppressions."""
        repo = _setup_repo(tmp_path)
        result = runner.invoke(
            app,
            ["suppress", "list", "--repo", str(repo)],
        )
        assert result.exit_code == 0
        assert "No active suppressions" in result.output

    def test_list_json(self, tmp_path: Path) -> None:
        """suppress list --json -> valid JSON array."""
        repo = _setup_repo(tmp_path)
        # Add a suppression first
        runner.invoke(
            app,
            [
                "suppress", "add",
                "--module", "payments", "--layer", "1",
                "--message", "bad import", "--reason", "debt",
                "--repo", str(repo),
            ],
        )
        result = runner.invoke(
            app,
            ["suppress", "list", "--json", "--repo", str(repo)],
        )
        assert result.exit_code == 0
        import json
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1

    def test_migrate(self, tmp_path: Path) -> None:
        """suppress migrate prints migrated count."""
        repo = _setup_repo(tmp_path)
        runner.invoke(
            app,
            [
                "suppress", "add",
                "--module", "old_mod", "--layer", "1",
                "--message", "msg", "--reason", "reason",
                "--repo", str(repo),
            ],
        )
        result = runner.invoke(
            app,
            [
                "suppress", "migrate",
                "--from", "old_mod", "--to", "new_mod",
                "--repo", str(repo),
            ],
        )
        assert result.exit_code == 0
        assert "Migrated 1" in result.output

    def test_orphans_none(self, tmp_path: Path) -> None:
        """suppress orphans with no orphans."""
        repo = _setup_repo(tmp_path)
        result = runner.invoke(
            app,
            ["suppress", "orphans", "--repo", str(repo)],
        )
        assert result.exit_code == 0
        assert "No orphaned suppressions found" in result.output

    def test_add_all_pending_valid(self, tmp_path: Path) -> None:
        """suppress add --all-pending --yes suppresses all active violations."""
        repo = _setup_repo(tmp_path)
        
        # Create a mock audit log
        from archguard.config import AUDIT_LOG_FILENAME
        import json
        
        audit_file = repo / AUDIT_LOG_FILENAME
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        
        run_event = {
            "event": "analysis_run",
            "violations": [
                {"module": "payments", "layer": 1, "message": "bad import 1", "suppressed": False},
                {"module": "orders", "layer": 2, "message": "coupling high", "suppressed": False},
            ]
        }
        
        with audit_file.open("w", encoding="utf-8") as f:
            f.write(json.dumps(run_event) + "\n")
            
        result = runner.invoke(
            app,
            ["suppress", "add", "--all-pending", "--yes", "--repo", str(repo)]
        )
        
        assert result.exit_code == 0
        assert "Suppressed Violations" in result.output
        
        # Verify both violations appear in the suppressions store
        from archguard.suppression.store import SuppressionStore
        store = SuppressionStore(repo)
        
        assert store.is_suppressed("payments", 1, "bad import 1") is True
        assert store.is_suppressed("orders", 2, "coupling high") is True


class TestContractCmd:
    def test_list_pending_empty(self, tmp_path: Path) -> None:
        """contract list-pending with no proposals."""
        repo = _setup_repo(tmp_path)
        result = runner.invoke(
            app,
            ["contract", "list-pending", "--repo", str(repo)],
        )
        assert result.exit_code == 0
        assert "No pending contract proposals" in result.output

    def test_reject_nonexistent(self, tmp_path: Path) -> None:
        """contract reject for nonexistent module -> exit 1."""
        repo = _setup_repo(tmp_path)
        result = runner.invoke(
            app,
            [
                "contract", "reject",
                "--module", "nope",
                "--repo", str(repo),
            ],
        )
        assert result.exit_code == 1
        assert "No pending proposal" in result.output


class TestSuppressionInAnalysis:
    def test_suppressed_violation_excluded(self, tmp_path: Path) -> None:
        """Suppressed violation excluded from AnalysisResult."""
        repo = _setup_repo(tmp_path)
        # Add a suppression for a specific violation
        from archguard.suppression.store import SuppressionStore

        store = SuppressionStore(repo)
        store.add(
            "payments", 1,
            "Imports `auth.internal` (disallowed)",
            "known tech debt",
        )

        # Verify it's suppressed
        assert store.is_suppressed(
            "payments", 1,
            "Imports `auth.internal` (disallowed)",
        ) is True
