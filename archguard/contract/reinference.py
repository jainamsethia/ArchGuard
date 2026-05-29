"""Pending contract proposal logic for reinference."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

from archguard.config import (
    EVENT_CONTRACT_PROPOSAL_EXPIRED,
    PENDING_CONTRACTS_DIR,
    SCHEMA_VERSION,
)

if TYPE_CHECKING:
    from archguard.audit.logger import AuditLogger

logger: logging.Logger = logging.getLogger(__name__)

PROPOSAL_STALENESS_DAYS: int = 7
PROPOSAL_FILENAME_PATTERN: str = "{module}.yml"


@dataclass
class ContractProposal:
    """A pending contract change proposal for a single module."""

    module_name: str
    proposed_path: str
    proposed_drift_threshold: float
    proposed_coupling_budget: int
    semantic_drift_score: float
    proposal_timestamp: str          # ISO8601 UTC — governs staleness
    source_commit: str


class ReinferenceEngine:
    """Manages pending contract proposals and staleness checks."""

    def __init__(
        self,
        repo_root: Path,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._repo_root = repo_root
        self._pending_dir = repo_root / PENDING_CONTRACTS_DIR
        self._audit: AuditLogger | None = audit_logger

    def should_propose(
        self,
        module_name: str,
        semantic_drift: float,
        drift_threshold: float,
    ) -> bool:
        """Return True if drift exceeds threshold and no pending proposal exists."""
        if semantic_drift <= drift_threshold:
            return False
        proposal_path = self._pending_dir / PROPOSAL_FILENAME_PATTERN.format(
            module=module_name,
        )
        return not proposal_path.exists()

    def create_proposal(
        self,
        module_name: str,
        semantic_drift: float,
        new_centroid_path: str,
        current_coupling_budget: int,
        source_commit: str,
    ) -> ContractProposal:
        """Create a pending proposal file. Does NOT overwrite if it exists."""
        proposal_path = self._pending_dir / PROPOSAL_FILENAME_PATTERN.format(
            module=module_name,
        )

        proposal = ContractProposal(
            module_name=module_name,
            proposed_path=new_centroid_path,
            proposed_drift_threshold=0.25,
            proposed_coupling_budget=current_coupling_budget,
            semantic_drift_score=semantic_drift,
            proposal_timestamp=datetime.now(timezone.utc).isoformat(),
            source_commit=source_commit,
        )

        if proposal_path.exists():
            # Never overwrite existing proposals
            return proposal

        self._pending_dir.mkdir(parents=True, exist_ok=True)

        data: dict[str, Any] = {
            "module_name": proposal.module_name,
            "proposed_path": proposal.proposed_path,
            "proposed_drift_threshold": proposal.proposed_drift_threshold,
            "proposed_coupling_budget": proposal.proposed_coupling_budget,
            "semantic_drift_score": proposal.semantic_drift_score,
            "proposal_timestamp": proposal.proposal_timestamp,
            "source_commit": proposal.source_commit,
        }

        with proposal_path.open("w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        return proposal

    def check_staleness(self) -> list[str]:
        """Scan pending proposals for staleness. Delete and log expired ones."""
        if not self._pending_dir.exists():
            return []

        expired: list[str] = []
        now = datetime.now(timezone.utc)
        cutoff = timedelta(days=PROPOSAL_STALENESS_DAYS)

        for path in sorted(self._pending_dir.glob("*.yml")):
            try:
                with path.open("r", encoding="utf-8") as f:
                    data: dict[str, Any] = yaml.safe_load(f)
                if not isinstance(data, dict):
                    continue

                ts_str = data.get("proposal_timestamp", "")
                ts = datetime.fromisoformat(str(ts_str))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)

                if now - ts > cutoff:
                    module_name = str(data.get("module_name", path.stem))
                    if self._audit:
                        self._audit.log(
                            EVENT_CONTRACT_PROPOSAL_EXPIRED,
                            module=module_name,
                        )
                    path.unlink()
                    expired.append(module_name)
            except Exception:  # noqa: BLE001
                logger.warning("Failed to check staleness for %s", path)
                continue

        return expired

    def accept_proposal(
        self,
        module_name: str,
        github_client: Any = None,
        repo_slug: str | None = None,
        branch: str = "main",
    ) -> bool:
        """Accept a pending proposal. Local mode writes directly to .archguard.yml."""
        proposal_path = self._pending_dir / PROPOSAL_FILENAME_PATTERN.format(
            module=module_name,
        )
        if not proposal_path.exists():
            return False

        try:
            with proposal_path.open("r", encoding="utf-8") as f:
                data: dict[str, Any] = yaml.safe_load(f)
            if not isinstance(data, dict):
                return False
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Analysis failed in accept_proposal file reading: {e}", exc_info=True)
            raise

        # Build updated module fragment
        updated_module: dict[str, Any] = {
            "name": module_name,
            "path": data.get("proposed_path", ""),
            "coupling_budget": data.get("proposed_coupling_budget", 3),
            "semantic_drift_threshold": data.get(
                "proposed_drift_threshold", 0.25,
            ),
        }

        if github_client is not None and repo_slug:
            # Phase 2: GitHub Contents API
            from ruamel.yaml import YAML
            import io
            ryaml = YAML()
            ryaml.preserve_quotes = True
            ryaml.default_flow_style = False
            ryaml.width = 4096
            try:
                repo = github_client.get_repo(repo_slug)
                config_path = ".archguard.yml"
                try:
                    contents = repo.get_contents(config_path, ref=branch)
                    existing = ryaml.load(contents.decoded_content)
                    if not isinstance(existing, dict):
                        existing = {"version": SCHEMA_VERSION, "modules": []}
                except Exception:  # noqa: BLE001
                    existing = {"version": SCHEMA_VERSION, "modules": []}

                # Update or add module
                modules = existing.get("modules", [])
                found = False
                for i, m in enumerate(modules):
                    if m.get("name") == module_name:
                        modules[i] = updated_module
                        found = True
                        break
                if not found:
                    modules.append(updated_module)
                existing["modules"] = modules

                buf = io.StringIO()
                ryaml.dump(existing, buf)
                new_content = buf.getvalue()
                try:
                    repo.update_file(
                        config_path,
                        f"archguard: accept contract for {module_name}",
                        new_content,
                        contents.sha,
                        branch=branch,
                    )
                except Exception:  # noqa: BLE001
                    repo.create_file(
                        config_path,
                        f"archguard: accept contract for {module_name}",
                        new_content,
                        branch=branch,
                    )

                proposal_path.unlink()
                return True
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Analysis failed in accept_proposal github mode: {e}", exc_info=True)
                raise
        else:
            # Local mode: write directly to .archguard.yml
            contract_path = self._repo_root / ".archguard.yml"
            from ruamel.yaml import YAML
            ryaml = YAML()
            ryaml.preserve_quotes = True
            ryaml.default_flow_style = False
            ryaml.width = 4096
            try:
                if contract_path.exists():
                    with contract_path.open("r", encoding="utf-8") as f:
                        existing = ryaml.load(f)
                    if not isinstance(existing, dict):
                        existing = {
                            "version": SCHEMA_VERSION,
                            "modules": [],
                        }
                else:
                    existing = {"version": SCHEMA_VERSION, "modules": []}

                modules = existing.get("modules", [])
                found = False
                for i, m in enumerate(modules):
                    if m.get("name") == module_name:
                        modules[i] = updated_module
                        found = True
                        break
                if not found:
                    modules.append(updated_module)
                existing["modules"] = modules

                with contract_path.open("w", encoding="utf-8") as f:
                    ryaml.dump(existing, f)

                proposal_path.unlink()
                return True
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Analysis failed in accept_proposal local mode: {e}", exc_info=True)
                raise

    def reject_proposal(self, module_name: str) -> bool:
        """Delete pending proposal file. Returns True if deleted."""
        proposal_path = self._pending_dir / PROPOSAL_FILENAME_PATTERN.format(
            module=module_name,
        )
        if not proposal_path.exists():
            return False
        proposal_path.unlink()
        return True

    def list_pending(self) -> list[ContractProposal]:
        """Return all pending proposals. Skips malformed files."""
        if not self._pending_dir.exists():
            return []

        results: list[ContractProposal] = []
        for path in sorted(self._pending_dir.glob("*.yml")):
            try:
                with path.open("r", encoding="utf-8") as f:
                    data: dict[str, Any] = yaml.safe_load(f)
                if not isinstance(data, dict):
                    continue
                results.append(ContractProposal(
                    module_name=str(data["module_name"]),
                    proposed_path=data.get("proposed_path", ""),
                    proposed_drift_threshold=float(
                        data.get("proposed_drift_threshold", 0.25),
                    ),
                    proposed_coupling_budget=int(
                        data.get("proposed_coupling_budget", 3),
                    ),
                    semantic_drift_score=float(
                        data.get("semantic_drift_score", 0.0),
                    ),
                    proposal_timestamp=str(data.get("proposal_timestamp", "")),
                    source_commit=str(data.get("source_commit", "unknown")),
                ))
            except Exception:  # noqa: BLE001
                logger.warning("Skipping malformed proposal: %s", path)
                continue

        return results

    def handle_deleted_comment(self, last_processed_id_path: Path) -> None:
        """Reset last_processed_comment_id to 0 in the state file."""
        last_processed_id_path.parent.mkdir(parents=True, exist_ok=True)
        state: dict[str, Any] = {}
        if last_processed_id_path.exists():
            try:
                with last_processed_id_path.open("r", encoding="utf-8") as f:
                    state = json.load(f)
            except Exception as e:  # noqa: BLE001
                import logging
                logging.getLogger(__name__).warning(f"Non-critical failure in handle_deleted_comment: {e}")
                state = {}

        state["last_processed_comment_id"] = 0
        with last_processed_id_path.open("w", encoding="utf-8") as f:
            json.dump(state, f)
