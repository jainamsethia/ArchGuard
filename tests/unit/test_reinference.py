from pathlib import Path
from archguard.contract.reinference import ReinferenceEngine


def test_accept_proposal_preserves_comments(tmp_path: Path):
    yml = tmp_path / ".archguard.yml"
    yml.write_text(
        "# My contract\nmodules:\n  # payments module\n  - name: payments\n    coupling_budget: 8\n",
        encoding="utf-8",
    )

    engine = ReinferenceEngine(tmp_path)

    # Create the pending proposal manually
    pending_dir = tmp_path / ".archguard-pending-contracts"
    pending_dir.mkdir(parents=True, exist_ok=True)
    proposal_file = pending_dir / "payments.yml"

    import yaml

    proposal_file.write_text(
        yaml.dump(
            {
                "module_name": "payments",
                "proposed_paths": [],
                "proposed_drift_threshold": 0.25,
                "proposed_coupling_budget": 10,
                "semantic_drift_score": 0.3,
                "proposal_timestamp": "2026-05-29T00:00:00Z",
                "source_commit": "abcdef",
            }
        ),
        encoding="utf-8",
    )

    engine.accept_proposal("payments")

    result = yml.read_text(encoding="utf-8")
    assert "# My contract" in result
    assert "# payments module" in result
    assert "coupling_budget: 10" in result

from unittest.mock import MagicMock

def test_accept_proposal_github(tmp_path: Path):
    engine = ReinferenceEngine(tmp_path)
    pending_dir = tmp_path / ".archguard-pending-contracts"
    pending_dir.mkdir(parents=True, exist_ok=True)
    proposal_file = pending_dir / "payments.yml"
    import yaml
    proposal_file.write_text(yaml.dump({"module_name": "payments"}), encoding="utf-8")

    mock_client = MagicMock()
    mock_repo = MagicMock()
    mock_client.get_repo.return_value = mock_repo
    mock_content = MagicMock()
    mock_content.decoded_content = b"version: 1.0\nmodules:\n  - name: payments\n    coupling_budget: 8\n"
    mock_content.sha = "1234"
    mock_repo.get_contents.return_value = mock_content

    engine.accept_proposal("payments", github_client=mock_client, repo_slug="org/repo", branch="main")

    mock_repo.update_file.assert_called_once()
    args, kwargs = mock_repo.update_file.call_args
    assert args[0] == ".archguard.yml"
    assert "accept contract for payments" in args[1]
    assert kwargs["branch"] == "main"
    assert not proposal_file.exists()

def test_accept_proposal_github_create(tmp_path: Path):
    engine = ReinferenceEngine(tmp_path)
    pending_dir = tmp_path / ".archguard-pending-contracts"
    pending_dir.mkdir(parents=True, exist_ok=True)
    proposal_file = pending_dir / "payments.yml"
    import yaml
    proposal_file.write_text(yaml.dump({"module_name": "payments"}), encoding="utf-8")

    mock_client = MagicMock()
    mock_repo = MagicMock()
    mock_client.get_repo.return_value = mock_repo
    
    # raise exception for get_contents
    mock_repo.get_contents.side_effect = Exception("Not found")

    engine.accept_proposal("payments", github_client=mock_client, repo_slug="org/repo", branch="main")

    # should call create_file
    mock_repo.create_file.assert_called_once()
    args, kwargs = mock_repo.create_file.call_args
    assert args[0] == ".archguard.yml"
    assert "accept contract for payments" in args[1]
    assert kwargs["branch"] == "main"
    assert not proposal_file.exists()

def test_list_pending_success(tmp_path: Path):
    engine = ReinferenceEngine(tmp_path)
    pending_dir = tmp_path / ".archguard-pending-contracts"
    pending_dir.mkdir(parents=True, exist_ok=True)
    proposal_file = pending_dir / "payments.yml"
    import yaml
    proposal_file.write_text(yaml.dump({
        "module_name": "payments",
        "proposed_drift_threshold": 0.25,
        "proposed_coupling_budget": 10,
        "semantic_drift_score": 0.3,
        "proposal_timestamp": "2026-05-29T00:00:00Z",
        "source_commit": "abcdef",
    }), encoding="utf-8")

    proposals = engine.list_pending()
    assert len(proposals) == 1
    assert proposals[0].module_name == "payments"
    assert proposals[0].proposed_coupling_budget == 10

def test_check_staleness(tmp_path: Path):
    engine = ReinferenceEngine(tmp_path)
    pending_dir = tmp_path / ".archguard-pending-contracts"
    pending_dir.mkdir(parents=True, exist_ok=True)
    proposal_file = pending_dir / "payments.yml"
    import yaml
    proposal_file.write_text(yaml.dump({
        "module_name": "payments",
        "proposal_timestamp": "2020-01-01T00:00:00Z"
    }), encoding="utf-8")
    
    expired = engine.check_staleness()
    assert len(expired) == 1
    assert "payments" in expired
    assert not proposal_file.exists()
