"""Unit tests for archguard.contract.reinference."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import yaml
import pytest

from archguard.config import EVENT_CONTRACT_PROPOSAL_EXPIRED
from archguard.contract.reinference import (
    PROPOSAL_STALENESS_DAYS,
    ContractProposal,
    ReinferenceEngine,
)


def _make_engine(tmp_path: Path, **kwargs: object) -> ReinferenceEngine:
    """Create a ReinferenceEngine in a temp dir."""
    return ReinferenceEngine(tmp_path, **kwargs)


def _write_proposal(
    tmp_path: Path,
    module: str,
    *,
    days_ago: int = 0,
    drift: float = 0.40,
) -> Path:
    """Write a pending proposal YAML file."""
    pending_dir = tmp_path / ".archguard-pending-contracts"
    pending_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    data = {
        "module_name": module,
        "proposed_paths": [f"{module}/"],
        "proposed_drift_threshold": 0.25,
        "proposed_coupling_budget": 5,
        "semantic_drift_score": drift,
        "proposal_timestamp": ts.isoformat(),
        "source_commit": "abc1234",
    }
    path = pending_dir / f"{module}.yml"
    with path.open("w") as f:
        yaml.dump(data, f, default_flow_style=False)
    return path


class TestShouldPropose:
    def test_true_when_drift_exceeds_threshold_no_file(
        self, tmp_path: Path,
    ) -> None:
        engine = _make_engine(tmp_path)
        assert engine.should_propose("payments", 0.40, 0.25) is True

    def test_false_when_drift_below_threshold(
        self, tmp_path: Path,
    ) -> None:
        engine = _make_engine(tmp_path)
        assert engine.should_propose("payments", 0.20, 0.25) is False

    def test_false_when_pending_exists(self, tmp_path: Path) -> None:
        _write_proposal(tmp_path, "payments")
        engine = _make_engine(tmp_path)
        assert engine.should_propose("payments", 0.40, 0.25) is False


class TestCreateProposal:
    def test_creates_file_returns_proposal(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path)
        result = engine.create_proposal(
            "orders", 0.35, ["orders/"], 4, "abc1234",
        )
        assert isinstance(result, ContractProposal)
        assert result.module_name == "orders"
        pending = tmp_path / ".archguard-pending-contracts" / "orders.yml"
        assert pending.exists()

    def test_second_call_is_noop(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path)
        engine.create_proposal("orders", 0.35, ["orders/"], 4, "abc1234")
        pending = tmp_path / ".archguard-pending-contracts" / "orders.yml"
        content_before = pending.read_text()
        # Second call — should not overwrite
        engine.create_proposal("orders", 0.50, ["orders/v2/"], 6, "def5678")
        assert pending.read_text() == content_before


class TestCheckStaleness:
    def test_fresh_proposal_not_expired(self, tmp_path: Path) -> None:
        _write_proposal(tmp_path, "auth", days_ago=6)
        engine = _make_engine(tmp_path)
        expired = engine.check_staleness()
        assert expired == []
        assert (
            tmp_path / ".archguard-pending-contracts" / "auth.yml"
        ).exists()

    def test_stale_proposal_expired_and_deleted(
        self, tmp_path: Path,
    ) -> None:
        _write_proposal(tmp_path, "auth", days_ago=8)
        engine = _make_engine(tmp_path)
        expired = engine.check_staleness()
        assert "auth" in expired
        assert not (
            tmp_path / ".archguard-pending-contracts" / "auth.yml"
        ).exists()

    def test_logs_event_on_expiry(self, tmp_path: Path) -> None:
        _write_proposal(tmp_path, "core", days_ago=8)
        mock_audit = MagicMock()
        engine = _make_engine(tmp_path, audit_logger=mock_audit)
        engine.check_staleness()
        mock_audit.log.assert_called_once()
        assert mock_audit.log.call_args[0][0] == EVENT_CONTRACT_PROPOSAL_EXPIRED

    def test_staleness_uses_proposal_timestamp_not_mtime(
        self, tmp_path: Path,
    ) -> None:
        """Staleness should be based on proposal_timestamp field, not file mtime."""
        # Write a proposal with timestamp 8 days ago
        path = _write_proposal(tmp_path, "data", days_ago=8)
        # Touch file to make mtime recent — should NOT prevent expiry
        import os
        os.utime(path, None)

        engine = _make_engine(tmp_path)
        expired = engine.check_staleness()
        assert "data" in expired


class TestAcceptReject:
    def test_accept_local_mode(self, tmp_path: Path) -> None:
        _write_proposal(tmp_path, "payments")
        engine = _make_engine(tmp_path)
        assert engine.accept_proposal("payments") is True
        # Pending file deleted
        assert not (
            tmp_path / ".archguard-pending-contracts" / "payments.yml"
        ).exists()
        # Contract file created
        contract_path = tmp_path / ".archguard.yml"
        assert contract_path.exists()
        with contract_path.open() as f:
            contract = yaml.safe_load(f)
        modules = contract.get("modules", [])
        assert any(m["name"] == "payments" for m in modules)

    def test_reject_deletes_file(self, tmp_path: Path) -> None:
        _write_proposal(tmp_path, "auth")
        engine = _make_engine(tmp_path)
        assert engine.reject_proposal("auth") is True
        assert not (
            tmp_path / ".archguard-pending-contracts" / "auth.yml"
        ).exists()

    def test_reject_nonexistent_returns_false(
        self, tmp_path: Path,
    ) -> None:
        engine = _make_engine(tmp_path)
        assert engine.reject_proposal("nope") is False

    def test_accept_github_mode_update(self, tmp_path: Path) -> None:
        _write_proposal(tmp_path, "payments")
        engine = _make_engine(tmp_path)
        mock_client = MagicMock()
        mock_repo = MagicMock()
        mock_client.get_repo.return_value = mock_repo
        mock_contents = MagicMock()
        mock_contents.decoded_content = b"schema_version: '3.0'\nmodules: []"
        mock_contents.sha = "sha123"
        mock_repo.get_contents.return_value = mock_contents
        
        assert engine.accept_proposal("payments", github_client=mock_client, repo_slug="org/repo") is True
        mock_repo.update_file.assert_called_once()

    def test_accept_github_mode_create(self, tmp_path: Path) -> None:
        _write_proposal(tmp_path, "payments")
        engine = _make_engine(tmp_path)
        mock_client = MagicMock()
        mock_repo = MagicMock()
        mock_client.get_repo.return_value = mock_repo
        mock_repo.get_contents.side_effect = Exception("Not found")
        mock_repo.update_file.side_effect = Exception("Not found")
        
        assert engine.accept_proposal("payments", github_client=mock_client, repo_slug="org/repo") is True
        mock_repo.create_file.assert_called_once()

    def test_accept_github_mode_failure(self, tmp_path: Path) -> None:
        _write_proposal(tmp_path, "payments")
        engine = _make_engine(tmp_path)
        mock_client = MagicMock()
        mock_repo = MagicMock()
        mock_client.get_repo.return_value = mock_repo
        mock_repo.get_contents.side_effect = Exception("Not found")
        mock_repo.update_file.side_effect = Exception("Not found")
        mock_repo.create_file.side_effect = Exception("Fails to create")
        
        assert engine.accept_proposal("payments", github_client=mock_client, repo_slug="org/repo") is False

class TestListPending:
    def test_list_pending_skips_malformed(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path)
        valid_path = _write_proposal(tmp_path, "valid")
        bad_path = tmp_path / ".archguard-pending-contracts" / "bad.yml"
        bad_path.write_text("{bad yaml")
        
        pending = engine.list_pending()
        assert len(pending) == 1
        assert pending[0].module_name == "valid"


class TestHandleDeletedComment:
    def test_resets_last_processed_id(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path)
        state_path = tmp_path / ".archguard-cache" / "comment_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with state_path.open("w") as f:
            json.dump({"last_processed_comment_id": 42}, f)

        engine.handle_deleted_comment(state_path)

        with state_path.open() as f:
            data = json.load(f)
        assert data["last_processed_comment_id"] == 0
