"""Output-correctness gate: verifies the analysis pipeline produces the
*correct* result (not just "doesn't crash") against fixture repos with
deliberately planted, known violations.

This complements ``scripts/smoke_test.sh`` (which only checks liveness,
security headers, and input rejection) by asserting that the actual score,
grade, and violation set match what's planted in each fixture. A regression
that silently changes the output will fail this test loudly.

It calls ``run_analysis_on_repo`` directly — the real pipeline_adapter the
dashboard uses — so the analysis (Layer 1-4, scoring, ViolationPayload
serialization, audit-log write) is exercised unmocked. The network/git-clone
layer is bypassed because correctness lives in the analysis, not the fetch.

Ground truth (verified by reading the fixture sources):
  - layer1_forbidden: api/routes.py:1 ``from db.models import User``;
    contract allows api only ``core`` → Layer 1 CRITICAL, file api/routes.py,
    line 1, module api.
  - layer2_coupling: core/main.py imports a,b,c,d → fan_out 4 > budget 3 →
    Layer 2 HIGH, module core, module-level (no line).
  - empty_repo: no .py files → skipped=True, score 0.0, grade F, zero
    violations (NOT a high score — the pipeline marks it skipped).
  - layer3_drift / planted_duplication: ML-gated. Without ML extras these
    layers are skipped (not errored) and emit no false-positive violations.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from archguard.audit.logger import AuditLogger
from archguard.config import AUDIT_LOG_FILENAME
from archguard.dashboard.pipeline_adapter import run_analysis_on_repo

FIXTURES = Path(__file__).parent.parent / "fixtures"

# Whether the ML extras (sentence-transformers + faiss-cpu) are installed.
# Layer 3 (semantic drift) and Layer 4 (duplication) are skipped without them.
try:
    import sentence_transformers  # noqa: F401
    import faiss  # noqa: F401

    _ML_AVAILABLE = True
except ImportError:
    _ML_AVAILABLE = False


async def _noop_progress(_msg: str) -> None:
    return None


def _run(fixture_name: str, tmp_path=None):
    """Run the real pipeline_adapter against a fixture and return (result, audit_run).

    The pipeline writes violations only to the audit log (AnalysisJobResult
    carries a count, not the list), so we read them back from
    ``<repo>/.archguard-cache/audit.jsonl`` to assert on their content.

    Runs against a *copy* of the fixture in a throwaway temp dir (created if no
    ``tmp_path`` is supplied) so the source fixture is never mutated — a prior
    run's audit log / embeddings DB can't leak into later tests (which caused
    an order-dependent failure in the full suite).
    """
    import asyncio
    import shutil
    import tempfile

    src = FIXTURES / fixture_name
    if tmp_path is not None:
        repo_path = tmp_path / fixture_name
        shutil.copytree(
            src, repo_path, ignore=shutil.ignore_patterns(".git", ".archguard-cache")
        )
        _cleanup = None
    else:
        # Self-managed temp dir for callers that aren't a pytest test function.
        repo_path = Path(tempfile.mkdtemp(prefix=f"ag-{fixture_name}-")) / fixture_name
        shutil.copytree(
            src, repo_path, ignore=shutil.ignore_patterns(".git", ".archguard-cache")
        )
        _cleanup = repo_path.parent

    job_id = "00000000-0000-0000-0000-000000000001"
    try:
        result = asyncio.run(
            run_analysis_on_repo(
                repo_path=repo_path,
                job_id=job_id,
                repo_url=f"https://github.com/test/{fixture_name}.git",
                progress_callback=_noop_progress,
                skip_explanation=True,
            )
        )
        audit_run = AuditLogger(repo_path / AUDIT_LOG_FILENAME).read_last_run()
        return result, audit_run
    finally:
        if _cleanup is not None:
            shutil.rmtree(_cleanup, ignore_errors=True)


def _violations(audit_run, layer: int | None = None):
    """Return violation dicts from an audit run, optionally filtered by layer."""
    if not audit_run:
        return []
    vs = audit_run.get("violations", [])
    if layer is None:
        return vs
    return [v for v in vs if int(v.get("layer", "0")) == layer]


def test_layer1_forbidden_detects_disallowed_import():
    """api/routes.py imports from db, which is not in api's allowed_imports."""
    result, audit_run = _run("layer1_forbidden")

    assert result.skipped is False
    layer1 = _violations(audit_run, layer=1)
    assert len(layer1) >= 1, "expected at least one Layer 1 violation"

    # The planted violation: api/routes.py:1 imports db.models.
    hit = next(
        (
            v
            for v in layer1
            if v.get("file") == "api/routes.py"
            and v.get("line") == 1
            and v.get("module") == "api"
        ),
        None,
    )
    assert hit is not None, (
        f"expected Layer 1 violation at api/routes.py:1 module api; got "
        f"{[(v.get('file'), v.get('line'), v.get('module'), v.get('message')) for v in layer1]}"
    )
    assert hit["severity"] == "critical"
    assert "db.models" in hit["message"]


def test_layer2_coupling_detects_fan_out_exceeds_budget():
    """core/main.py imports from a,b,c,d → fan_out 4 > budget 3."""
    result, audit_run = _run("layer2_coupling")

    assert result.skipped is False
    layer2 = _violations(audit_run, layer=2)
    assert len(layer2) >= 1, "expected at least one Layer 2 violation"

    hit = next((v for v in layer2 if v.get("module") == "core"), None)
    assert hit is not None, (
        f"expected Layer 2 violation on module core; got "
        f"{[(v.get('module'), v.get('message')) for v in layer2]}"
    )
    # Layer 2 is module-level: no single line.
    assert hit.get("file") in (None, "")
    assert hit.get("line", 0) == 0
    assert "fan_out" in hit["message"] and "budget" in hit["message"]


def test_empty_repo_is_skipped_with_zero_violations():
    """A repo with no Python files is skipped, not scored high.

    NOTE: this intentionally asserts the real behavior (skipped=True, score
    0.0, grade F, zero violations) — not a "high score." The dashboard now
    surfaces this as an explicit skipped state (Phase 5b).
    """
    result, audit_run = _run("empty_repo")

    assert result.skipped is True
    assert result.health_score == 0.0
    assert result.health_grade == "F"
    assert result.total_violations == 0
    assert "No Python files" in (result.skip_reason or "")
    # No false-positive violations of any layer.
    assert _violations(audit_run) == []


@pytest.mark.skipif(_ML_AVAILABLE, reason="ML extras installed; this asserts the no-ML path")
def test_ml_gated_layers_skip_cleanly_without_false_positives():
    """Without ML extras, Layers 3 and 4 must skip (not error) and emit no
    false-positive violations on fixtures that would otherwise require them."""
    # layer3_drift fixture: no cached baseline → drift 0 → no violation.
    r3, a3 = _run("layer3_drift")
    assert r3.error is None, f"Layer 3 should skip cleanly, not error: {r3.error}"
    assert _violations(a3, layer=3) == [], "Layer 3 must not emit false positives when skipped"

    # planted_duplication: Layer 4 skips without ML, no false positives.
    r4, a4 = _run("planted_duplication")
    assert r4.error is None, f"Layer 4 should skip cleanly, not error: {r4.error}"
    assert _violations(a4, layer=4) == [], "Layer 4 must not emit false positives when skipped"


@pytest.mark.skipif(not _ML_AVAILABLE, reason="requires ML extras (sentence-transformers + faiss)")
def test_planted_duplication_intra_module_no_cross_module_violation(tmp_path):
    """planted_duplication's own contract declares a single ``misc`` module
    (path ``./``), so both near-duplicate functions live in the SAME module.

    Layer 4 reports CROSS-MODULE clones only by design (it explicitly skips
    matches within a module's own files). So zero Layer-4 violations here is
    CORRECT — the duplication is intra-module, not a false negative. This test
    pins that correct behavior so it can't silently flip.
    """
    import shutil

    src = FIXTURES / "planted_duplication"
    repo = tmp_path / "repo"
    shutil.copytree(src, repo, ignore=shutil.ignore_patterns(".git", ".archguard-cache"))

    import asyncio
    result = asyncio.run(
        run_analysis_on_repo(
            repo_path=repo,
            job_id="00000000-0000-0000-0000-000000000021",
            repo_url="https://github.com/test/intra.git",
            progress_callback=_noop_progress,
            skip_explanation=True,
        )
    )
    audit_run = AuditLogger(repo / AUDIT_LOG_FILENAME).read_last_run()
    assert result.skipped is False
    assert result.error is None, f"Layer 4 should not error: {result.error}"
    assert _violations(audit_run, layer=4) == [], (
        "single-module contract must yield zero cross-module duplication violations"
    )


@pytest.mark.skipif(not _ML_AVAILABLE, reason="requires ML extras (sentence-transformers + faiss)")
def test_cross_module_duplication_is_detected(tmp_path):
    """With ML extras and a CROSS-MODULE contract, Layer 4 detects the clone.

    This is the regression test for a real path-normalization bug: a CWD-relative
    ``changed_files`` list left ``affected`` empty and starved Layers 3 & 4, and
    Layer 4 queried the embedding cache with CWD-relative file paths while Layer
    3 had stored embeddings under repo-root-relative keys — so no match was ever
    found. Both bugs are fixed in ``_orchestrator_utils._get_affected_modules``
    and ``_layer_runners._run_layer4``. This test fails loudly if either regresses
    and cross-module duplication silently stops being detected.
    """
    import asyncio
    import shutil

    src = FIXTURES / "planted_duplication"
    repo = tmp_path / "repo"
    shutil.copytree(src, repo, ignore=shutil.ignore_patterns(".git", ".archguard-cache"))
    # Split the two files into separate modules so the clone is CROSS-module.
    (repo / ".archguard.yml").write_text(
        "version: '3.0'\nmodules:\n- name: module_a\n  path: module_a/\n"
        "- name: module_b\n  path: module_b/\n",
        encoding="utf-8",
    )

    asyncio.run(
        run_analysis_on_repo(
            repo_path=repo,
            job_id="00000000-0000-0000-0000-000000000022",
            repo_url="https://github.com/test/cross.git",
            progress_callback=_noop_progress,
            skip_explanation=True,
        )
    )
    audit_run = AuditLogger(repo / AUDIT_LOG_FILENAME).read_last_run()
    layer4 = _violations(audit_run, layer=4)
    assert len(layer4) >= 1, (
        "expected Layer 4 cross-module duplication violation; "
        "path normalization between Layers 3 and 4 may have regressed"
    )
    assert "module_a/a.py" in layer4[0]["message"]
    assert "module_b/b.py" in layer4[0]["message"]
