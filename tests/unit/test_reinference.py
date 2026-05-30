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
