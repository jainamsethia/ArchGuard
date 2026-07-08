import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from archguard.contract.reinference import ReinferenceEngine, ContractProposal


def test_should_propose(tmp_path):
    engine = ReinferenceEngine(repo_root=tmp_path)
    
    # Below threshold -> False
    assert not engine.should_propose("auth", 0.1, 0.2)
    
    # Above threshold -> True
    assert engine.should_propose("auth", 0.3, 0.2)
    
    # If proposal file exists -> False
    engine.create_proposal("auth", 0.3, "src/auth", 5, "commit123")
    assert not engine.should_propose("auth", 0.3, 0.2)


def test_create_proposal(tmp_path):
    engine = ReinferenceEngine(repo_root=tmp_path)
    proposal = engine.create_proposal("auth", 0.5, "src/auth", 10, "sha123")
    
    assert proposal.module_name == "auth"
    assert proposal.proposed_path == "src/auth"
    assert proposal.semantic_drift_score == 0.5
    
    # Ensure it doesn't overwrite
    proposal2 = engine.create_proposal("auth", 0.9, "src/auth2", 20, "sha456")
    # File content should remain the first one
    from archguard.config import PENDING_CONTRACTS_DIR
    import yaml
    with open(tmp_path / PENDING_CONTRACTS_DIR / "auth.yml") as f:
        data = yaml.safe_load(f)
    assert data["source_commit"] == "sha123"


def test_check_staleness(tmp_path):
    engine = ReinferenceEngine(repo_root=tmp_path)
    
    # create one fresh and one stale manually
    from archguard.config import PENDING_CONTRACTS_DIR
    import yaml
    import os
    
    pending_dir = tmp_path / PENDING_CONTRACTS_DIR
    pending_dir.mkdir(parents=True)
    
    # stale
    stale_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    with open(pending_dir / "stale.yml", "w") as f:
        yaml.safe_dump({"proposal_timestamp": stale_date}, f)
        
    # fresh
    fresh_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with open(pending_dir / "fresh.yml", "w") as f:
        yaml.safe_dump({"proposal_timestamp": fresh_date}, f)
        
    engine.check_staleness()
    
    assert not (pending_dir / "stale.yml").exists()
    assert (pending_dir / "fresh.yml").exists()
