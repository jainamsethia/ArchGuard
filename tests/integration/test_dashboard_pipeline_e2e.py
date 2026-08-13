"""End-to-end regression test for the dashboard's analysis job.

Runs a real public repository through the *actual* job path -- the same clone
``temp_workspace`` makes and the same ``run_analysis_on_repo`` the job manager
calls -- then reads back the persisted run the API serves and checks it is
internally consistent.

This is the test that would have caught the original defect: on a blobless
clone, contract generation used to fail to read commit history and silently
score the repository against modules guessed from folder names.

Marked ``integration`` because it clones over the network; it skips rather than
fails when the network or git is unavailable.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from archguard.audit.logger import AuditLogger
from archguard.dashboard.pipeline_adapter import run_analysis_on_repo
from archguard.dashboard.workspace import temp_workspace

# Small, stable, permissively licensed, and rarely force-pushed.
REPO_URL = "https://github.com/benjaminp/six.git"


def _run_pipeline() -> tuple[object, dict | None]:
    job_id = uuid.uuid4().hex[:12]

    async def _go():
        async with temp_workspace(REPO_URL, job_id=job_id, keep_alive=False) as repo:
            result = await run_analysis_on_repo(
                repo, job_id=job_id, repo_url=REPO_URL,
            )
            persisted = next(
                (
                    r
                    for r in reversed(
                        AuditLogger(
                            repo / ".archguard-cache" / "audit.jsonl"
                        ).read_last_n_runs(n=50)
                    )
                    if r.get("job_id") == job_id
                ),
                None,
            )
            return result, persisted

    return asyncio.run(_go())


@pytest.fixture(scope="module")
def pipeline_run():
    try:
        return _run_pipeline()
    except (RuntimeError, TimeoutError, OSError) as exc:
        pytest.skip(f"network/git unavailable for end-to-end clone: {exc}")


@pytest.mark.integration
def test_pipeline_completes_without_error(pipeline_run):
    result, _ = pipeline_run

    assert result.error is None, f"analysis reported an error: {result.error}"
    assert result.skipped is False
    assert 0.0 <= result.health_score <= 100.0
    assert result.health_grade in {"A", "B", "C", "D", "F"}


@pytest.mark.integration
def test_pipeline_persists_a_run_for_its_job(pipeline_run):
    """A completed analysis must leave a run the API can serve.

    The audit write is wrapped in a broad handler; if it fails the analysis
    still "succeeds" while every read endpoint reports no data.
    """
    _, persisted = pipeline_run

    assert persisted is not None, "analysis completed but no run was persisted"
    assert persisted.get("score") is not None


@pytest.mark.integration
def test_persisted_run_is_internally_consistent(pipeline_run):
    _, persisted = pipeline_run
    assert persisted is not None

    violations = persisted.get("violations", [])
    layer_results = persisted.get("layer_results", [])

    # Every violation belongs to exactly one layer, so the per-layer counts
    # must account for all of them.
    assert sum(lr["violation_count"] for lr in layer_results) == len(violations)

    # The module map the scores are keyed by must be the one that was analysed.
    assert set(persisted.get("module_scores", {})) == set(
        persisted.get("modules_analyzed", [])
    )

    # Every violation attributed to a module must name a module we analysed.
    known = set(persisted.get("modules_analyzed", []))
    for v in violations:
        if v.get("module"):
            assert v["module"] in known, f"violation names unknown module {v['module']}"


@pytest.mark.integration
def test_provenance_flags_are_persisted_and_coherent(pipeline_run):
    """The provenance flags must be present and agree with the contract."""
    result, persisted = pipeline_run
    assert persisted is not None

    assert "fallback_directory_heuristic" in persisted
    assert "contract_auto_generated" in persisted

    # six.git ships no .archguard.yml, so one must have been generated.
    assert persisted["contract_auto_generated"] is True
    assert persisted["fallback_directory_heuristic"] == result.fallback_directory_heuristic

    # The flag must agree with the contract it was derived from.
    generated_by = str(persisted.get("contract", {}).get("generated_by", ""))
    assert persisted["fallback_directory_heuristic"] == ("fallback" in generated_by)


@pytest.mark.integration
def test_real_history_is_used_for_a_repo_with_history(pipeline_run):
    """six.git has hundreds of commits, so boundaries must be measured.

    This pins the actual fix: before it, the blobless clone made history
    extraction fail (or take minutes), forcing the directory-name heuristic.
    """
    _, persisted = pipeline_run
    assert persisted is not None

    assert persisted["fallback_directory_heuristic"] is False, (
        "a repo with full commit history fell back to guessing modules from "
        f"folder names: {persisted.get('fallback_reason')!r}"
    )
