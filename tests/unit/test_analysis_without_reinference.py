"""The analysis pipeline no longer runs contract re-inference.

Re-inference was live on the website's primary path -- `_run_reinference` was
called unconditionally at the end of the Layer 3 stage, on every analysis. It
was not dead code. It was worse than dead code: it ran, and its output went
somewhere nothing could read.

Verified by instrumenting a real analysis before removal:

    _run_reinference called : 1
    ReinferenceEngine built : 1
    proposals written       : 0
    proposal files in clone : 0

`ReinferenceEngine` writes proposals to `repo_root / .archguard-pending-contracts`
-- inside the git clone the dashboard makes per job and then deletes. Nothing in
any route, worker task or dashboard flow reads that directory. The accept path
that would have consumed a proposal (`accept_proposal`, and
`handle_deleted_comment` for GitHub PR comments) had no source callers at all:
it belonged to the CLI workflow removed in P1-1.

So the capability could not affect anything a user sees, while still costing an
engine construction per analysis and -- because every failure inside it was
re-raised as `AnalysisError("Reinference check failed")` -- carrying the power
to fail an entire analysis for a feature with no observable output.

The idea is worth keeping in mind for a website that stores runs: "your
contract no longer matches your code, here is a proposed update" is a real
thing to offer a watched repository. Doing it would mean persisting proposals to
PostgreSQL and surfacing them, which is a product feature rather than a
refactor. Tracked separately; not built here.
"""

from __future__ import annotations

import importlib

import pytest


def test_the_reinference_modules_are_gone():
    """Both halves of the chain, not just the entry point."""
    for name in (
        "archguard.analysis._reinference",
        "archguard.contract.reinference",
    ):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)


def test_the_layer_stage_no_longer_calls_it():
    """The call site was unconditional, so a leftover import would still run."""
    import inspect

    from archguard.analysis import _orchestrator_stages

    source = inspect.getsource(_orchestrator_stages)
    assert "_run_reinference" not in source, (
        "the Layer 3 stage still reaches for re-inference"
    )
    assert "reinference" not in source.lower()


def test_nothing_in_the_package_imports_it():
    """A module that only tests import is how this survived the CLI removal."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "archguard"
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "reinference" in path.read_text(encoding="utf-8").lower()
    ]
    assert offenders == [], f"re-inference is still referenced by {offenders}"


def test_an_analysis_still_completes(tmp_path):
    """The point of the removal: the pipeline it was wired into still runs.

    A small real repository through the same orchestrator the worker uses, so
    this fails if taking the stage out broke the Layer 3 path it sat at the end
    of.
    """
    import subprocess

    from archguard.analysis.layers import AnalysisOrchestrator

    repo = tmp_path / "proj"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg" / "core.py").write_text("import os\n", encoding="utf-8")
    (repo / "pkg" / "api.py").write_text("from pkg import core\n", encoding="utf-8")
    (repo / ".archguard.yml").write_text(
        "version: '3.0'\nmodules:\n- name: pkg\n  path: pkg/\n  coupling_budget: 5\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)

    files = sorted(repo.rglob("*.py"))
    result = AnalysisOrchestrator(repo).run(files, "0000000")

    assert result is not None
    assert result.skipped is False, f"analysis was skipped: {result.skip_reason}"
    # A module was actually measured, so this is not passing on an empty run.
    assert result.layer_scores is not None
    assert result.modules_analyzed, "no module was analysed"


def test_no_proposal_directory_is_created(tmp_path):
    """Re-inference used to write into the analysed checkout.

    On the website that was a throwaway clone, but it is still someone's
    working tree when the orchestrator is pointed at a real one -- and
    `check_staleness` deleted files from it.
    """
    import subprocess

    from archguard.analysis.layers import AnalysisOrchestrator

    repo = tmp_path / "proj"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg" / "core.py").write_text("import os\n", encoding="utf-8")
    (repo / ".archguard.yml").write_text(
        "version: '3.0'\nmodules:\n- name: pkg\n  path: pkg/\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)

    AnalysisOrchestrator(repo).run(sorted(repo.rglob("*.py")), "0000000")

    assert not (repo / ".archguard-pending-contracts").exists()
    assert not list(repo.glob(".archguard/pending_contracts*"))


def test_the_dead_llm_contract_inference_chain_is_gone():
    """`contract.llm_inference` was imported by tests and nothing else, and it
    was the only source consumer of `llm.cloud`.

    P1-4 did not revive LLM violation explanations for the website, which was
    the condition the plan set for keeping `llm/cloud.py`.
    """
    for name in ("archguard.contract.llm_inference", "archguard.llm.cloud"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)


def test_the_live_ai_features_are_untouched():
    """The Advisor and remediation are a different code path entirely, and both
    are supported website features. Removing the explainer must not touch them.
    """
    for name in (
        "archguard.llm.advisor",
        "archguard.llm.remediation",
        "archguard.llm.gemini",
    ):
        assert importlib.import_module(name) is not None
