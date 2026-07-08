from pathlib import Path
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner
from archguard.cli.contract_cmd import contract_app

runner = CliRunner()

@patch("archguard.cli.contract_cmd.ReinferenceEngine")
def test_contract_list_pending_empty(mock_engine_cls, tmp_path):
    mock_engine = MagicMock()
    mock_engine.list_pending.return_value = {}
    mock_engine_cls.return_value = mock_engine
    
    result = runner.invoke(contract_app, ["list-pending", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "No pending contract proposals." in result.stdout

@patch("archguard.cli.contract_cmd.ReinferenceEngine")
def test_contract_list_pending_with_proposals(mock_engine_cls, tmp_path):
    mock_engine = MagicMock()
    mock_proposal = MagicMock()
    mock_proposal.module_name = "module1"
    mock_proposal.semantic_drift_score = 5.0
    mock_proposal.proposed_drift_threshold = 10.0
    mock_proposal.proposed_coupling_budget = 20
    mock_proposal.proposal_timestamp = "2026-07-06T00:00:00Z"
    
    mock_engine.list_pending.return_value = [mock_proposal]
    mock_engine_cls.return_value = mock_engine
    
    result = runner.invoke(contract_app, ["list-pending", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "module1" in result.stdout
    assert "5.0" in result.stdout
    assert "10.0" in result.stdout

@patch("archguard.cli.contract_cmd.ReinferenceEngine")
def test_contract_accept_success(mock_engine_cls, tmp_path):
    mock_engine = MagicMock()
    mock_engine.accept_proposal.return_value = True
    mock_engine_cls.return_value = mock_engine
    
    result = runner.invoke(contract_app, ["accept", "--module", "module1", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "Contract proposal for 'module1' accepted and written to .archguard.yml" in result.stdout

@patch("archguard.cli.contract_cmd.ReinferenceEngine")
def test_contract_reject_success(mock_engine_cls, tmp_path):
    mock_engine = MagicMock()
    mock_engine.reject_proposal.return_value = True
    mock_engine_cls.return_value = mock_engine
    
    result = runner.invoke(contract_app, ["reject", "--module", "module1", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "Contract proposal for 'module1' rejected and removed." in result.stdout

@patch("archguard.cli.contract_cmd.ReinferenceEngine")
def test_contract_show_success(mock_engine_cls, tmp_path):
    mock_engine = MagicMock()
    mock_proposal = MagicMock()
    mock_proposal.module_name = "module1"
    mock_proposal.proposed_path = "src/module1"
    mock_proposal.proposed_drift_threshold = 10.0
    mock_proposal.proposed_coupling_budget = 20
    mock_proposal.semantic_drift_score = 5.0
    mock_proposal.proposal_timestamp = "2026-07-06T00:00:00Z"
    mock_proposal.source_commit = "abcdef"
    mock_engine.list_pending.return_value = [mock_proposal]
    mock_engine_cls.return_value = mock_engine

    result = runner.invoke(contract_app, ["show", "--module", "module1", "--repo", str(tmp_path)])
    assert result.exit_code == 0
    assert "module1" in result.stdout
    assert "abcdef" in result.stdout

@patch("archguard.cli.contract_cmd.ReinferenceEngine")
def test_contract_show_not_found(mock_engine_cls, tmp_path):
    mock_engine = MagicMock()
    mock_engine.list_pending.return_value = []
    mock_engine_cls.return_value = mock_engine

    result = runner.invoke(contract_app, ["show", "--module", "module1", "--repo", str(tmp_path)])
    assert result.exit_code != 0
    assert "No pending proposal found for module 'module1'" in result.stdout
