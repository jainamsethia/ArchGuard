"""Suppression dataclasses and serialization helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Suppression:
    """A single violation suppression record."""

    id: str                    # UUID4
    module: str                # module name from contract
    layer: int                 # 1, 2, 3, or 4
    violation_hash: str        # SHA256 of (module + layer + message)
    reason: str                # max 500 chars, no newlines
    created_at: str            # ISO8601 UTC
    created_by: str            # GitHub username or "local"
    expires_at: str | None     # ISO8601 UTC or None = never
    pr_number: int | None      # PR that triggered suppression
    commit_sha: str            # 7-char short SHA
    active: bool = True        # False = orphaned


def make_violation_hash(module: str, layer: int, message: str) -> str:
    """SHA256 of ``f"{module}:{layer}:{message}"``."""
    raw = f"{module}:{layer}:{message}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def suppression_to_jsonl(s: Suppression) -> str:
    """Serialize to single JSON line (no trailing newline)."""
    data: dict[str, Any] = {
        "id": s.id,
        "module": s.module,
        "layer": s.layer,
        "violation_hash": s.violation_hash,
        "reason": s.reason,
        "created_at": s.created_at,
        "created_by": s.created_by,
        "expires_at": s.expires_at,
        "pr_number": s.pr_number,
        "commit_sha": s.commit_sha,
        "active": s.active,
    }
    return json.dumps(data, separators=(",", ":"))


def suppression_from_dict(d: dict[str, Any]) -> Suppression:
    """Deserialize from dict parsed from JSONL line.

    Handles missing optional fields gracefully.
    """
    return Suppression(
        id=str(d["id"]),
        module=str(d["module"]),
        layer=int(d["layer"]),
        violation_hash=str(d["violation_hash"]),
        reason=str(d["reason"]),
        created_at=str(d["created_at"]),
        created_by=str(d.get("created_by", "local")),
        expires_at=d.get("expires_at"),
        pr_number=d.get("pr_number"),
        commit_sha=str(d.get("commit_sha", "unknown")),
        active=bool(d.get("active", True)),
    )
