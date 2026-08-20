"""Regression tests for module-boundary provenance.

Background: contract generation derives module boundaries from co-change
history.  When that history cannot be read, it silently fell back to guessing
modules from top-level directory names, and nothing downstream said so -- the
dashboard reported a health score, grade and violations computed against a
guessed module map as though they had been measured.

These tests pin the three links in that chain:
  1. history extraction reports *why* it failed instead of collapsing to
     "this repo has 0 commits";
  2. the fallback is reported as used whenever it actually ran;
  3. the flag survives persistence and reaches the /api/v1/runs* endpoints.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from archguard.contract._discovery import (
    HISTORY_OK,
    HISTORY_UNAVAILABLE,
)
from archguard.contract._discovery import (
    detect_module_communities as _phase3_communities,
)
from archguard.contract._discovery import (
    scan_commit_history as _phase2_commits,
)


def _empty_graph_data() -> dict:
    """An empty co-change graph, serialised the way _phase2_commits serialises."""
    import networkx as nx

    return nx.node_link_data(nx.Graph())


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    )


@pytest.fixture
def tiny_repo(tmp_path: Path) -> Path:
    """A real git repo with a single commit and a nested package layout."""
    repo = tmp_path / "tiny"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg" / "core.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (repo / "pkg" / "util.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "initial")
    return repo


# ---------------------------------------------------------------------------
# 1. Failure is reported as failure, not as "no history"
# ---------------------------------------------------------------------------


def test_history_extraction_failure_is_distinguished_from_sparse_history(tmp_path):
    """A directory that is not a git repo must report history_status=unavailable.

    Previously any exception set commits=[] so commit_count became 0, which is
    indistinguishable from a genuinely brand-new repository.
    """
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    (not_a_repo / "mod.py").write_text("x = 1\n", encoding="utf-8")

    data = _phase2_commits(not_a_repo)

    assert data["history_status"] == HISTORY_UNAVAILABLE
    assert data["history_error"], "the underlying error must be recorded, not swallowed"
    assert data["commits_analyzed"] == 0


def test_history_extraction_succeeds_on_real_repo(tiny_repo):
    data = _phase2_commits(tiny_repo)

    assert data["history_status"] == HISTORY_OK
    assert data["history_error"] == ""
    assert data["commit_count"] == 1


def test_cochange_graph_nodes_are_repo_relative_paths(tiny_repo):
    """Graph nodes must be paths, not basenames.

    Basenames collapse every ``__init__.py`` into one node and leave
    ``_infer_path`` with no directory to infer, which produced contracts where
    every module pointed at the repo root.
    """
    data = _phase2_commits(tiny_repo)
    nodes = [n["id"] for n in data["graph_data"]["nodes"]]

    assert nodes, "single commit touching 3 .py files must produce co-change edges"
    assert all("/" in n for n in nodes), f"expected repo-relative paths, got {nodes}"
    assert "pkg/core.py" in nodes


# ---------------------------------------------------------------------------
# 2. The fallback reports itself whenever it runs
# ---------------------------------------------------------------------------


def test_unavailable_history_reports_fallback_with_reason(tiny_repo):
    result = _phase3_communities(
        _empty_graph_data(), tiny_repo, [], commit_count=0, min_history=1,
        history_status=HISTORY_UNAVAILABLE, history_error="boom",
    )

    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "history_unavailable"


def test_sparse_history_reports_fallback_with_reason(tiny_repo):
    result = _phase3_communities(
        _empty_graph_data(), tiny_repo, [], commit_count=0, min_history=5,
        history_status=HISTORY_OK,
    )

    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "sparse_history"


def test_empty_cochange_graph_reports_fallback(tiny_repo):
    """History read fine but yielded no separable communities.

    This is the case the old code got wrong: it recomputed ``fallback_used``
    from the *post*-fallback community count, so whenever the directory
    heuristic happened to return >= 2 modules the fallback reported False.
    """
    result = _phase3_communities(
        _empty_graph_data(), tiny_repo, [], commit_count=100, min_history=1,
        history_status=HISTORY_OK,
    )

    assert result["fallback_used"] is True
    assert result["fallback_reason"] == "low_community_diversity"
    # Guard the exact regression: the heuristic returned modules, and the flag
    # must still be True.
    assert result["num_communities"] >= 1


# ---------------------------------------------------------------------------
# 3. The flag survives persistence and reaches the API
# ---------------------------------------------------------------------------


def test_payload_carries_provenance_fields():
    from archguard.dashboard._result_schema import AnalysisResultPayload

    payload = AnalysisResultPayload(
        job_id="j1", score=88.0, band="PASS", violations=[],
        fallback_directory_heuristic=True,
        contract_auto_generated=True,
        fallback_reason="archguard init (directory heuristic fallback: sparse_history)",
    )
    dumped = payload.model_dump()

    assert dumped["fallback_directory_heuristic"] is True
    assert dumped["contract_auto_generated"] is True
    assert "sparse_history" in dumped["fallback_reason"]


def test_fallback_flag_is_visible_in_runs_api(monkeypatch, tmp_path):
    """The flag must reach /api/v1/runs, not stop at the pipeline adapter."""
    from archguard.audit.logger import AuditLogger
    from archguard.dashboard._result_schema import AnalysisResultPayload
    from archguard.dashboard.routes import runs as runs_route

    job_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    log_path = tmp_path / "audit.jsonl"

    payload = AnalysisResultPayload(
        job_id=job_id, score=72.5, band="WARN", violations=[],
        modules_analyzed=["docs", "src"],
        module_scores={"docs": 100.0, "src": 70.0},
        fallback_directory_heuristic=True,
        contract_auto_generated=True,
        fallback_reason="archguard init (directory heuristic fallback: history_unavailable)",
    )
    AuditLogger(log_path).log("analysis_run", **payload.model_dump())
    monkeypatch.setattr(runs_route, "get_audit_path", lambda jid: log_path)

    body = runs_route.get_runs(limit=50, job_id=job_id)
    assert len(body["runs"]) == 1
    run = body["runs"][0]

    assert run["fallback_directory_heuristic"] is True
    assert run["contract_auto_generated"] is True
    assert "history_unavailable" in run["fallback_reason"]

    latest = runs_route.get_latest_run(job_id=job_id)
    assert latest["fallback_directory_heuristic"] is True
