"""Coverage tests for CLI commands (LOW-004)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer


def test_sync_cache_coverage() -> None:
    import sys

    from archguard.cli.sync_cmd import SyncDirection, sync_cache
    sys.modules["boto3"] = MagicMock()
    # push path
    sync_cache(direction=SyncDirection.push, bucket="test-bucket", prefix="pre", cache_dir=Path(".test-cache"), profile=None)
    # pull path
    sync_cache(direction=SyncDirection.pull, bucket="test-bucket", prefix="pre", cache_dir=Path(".test-cache"), profile=None)
    del sys.modules["boto3"]

def test_diff_cmd_coverage() -> None:
    from archguard.cli.diff_cmd import diff_cmd

    with patch("archguard.cli.diff_cmd.AuditLogger") as mock_logger:
        mock_logger.return_value.read_last_n_runs.return_value = []
        with pytest.raises(typer.Exit):
            diff_cmd(repo=".", runs=2, json_output=False)

        mock_logger.return_value.read_last_n_runs.return_value = [{"score": 10}, {"score": 20, "violations": [{"layer": "l1"}]}]
        diff_cmd(repo=".", runs=2, json_output=True)
        diff_cmd(repo=".", runs=2, json_output=False)

def test_contract_cmd_list_pending() -> None:
    from archguard.cli.contract_cmd import contract_list_pending
    with patch("archguard.cli.contract_cmd.ReinferenceEngine") as mock_engine:
        mock_engine.return_value.list_pending.return_value = []
        contract_list_pending(repo=Path("."))

        mock_p = MagicMock()
        mock_p.proposal_timestamp = "2023-01-01T00:00:00Z"
        mock_p.semantic_drift_score = 0.5
        mock_p.proposed_drift_threshold = 0.6
        mock_p.proposed_coupling_budget = 10
        mock_p.module_name = "test"
        mock_engine.return_value.list_pending.return_value = [mock_p]
        contract_list_pending(repo=Path("."))

def test_analyze_watch_coverage() -> None:
    from archguard.cli._analyze_watch import AnalysisEventHandler
    opts = MagicMock()
    console = MagicMock()
    handler = AnalysisEventHandler(opts, console)

    event = MagicMock()
    event.src_path = "test.py"
    event.is_directory = False

    with patch("threading.Timer") as mock_timer:
        handler.on_modified(event)
        handler.on_modified(event) # debounce test

    with patch("archguard.cli._analyze_watch._analyze_command_impl") as mock_impl:
        mock_result = MagicMock()
        mock_result.archdebt.composite_score = 0.5
        mock_impl.return_value = (0, mock_result)
        handler._run_analysis()
        mock_result.archdebt.composite_score = 0.6
        handler._run_analysis()

def test_contract_accept() -> None:
    from archguard.cli.contract_cmd import contract_accept
    with patch("archguard.contract.reinference.ReinferenceEngine") as mock_engine:
        with pytest.raises(typer.Exit) as e:
            contract_accept(module="mod", repo_slug=None, branch="main", repo=Path("."))
        assert e.type == typer.Exit


