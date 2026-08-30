"""Driving a real analysis the way a job does, for incremental tests.

Not a test module (no ``test_`` prefix, so pytest does not collect it). It
exists because more than one suite needs to ask the same question -- what does
the product actually persist when this repository is scanned twice? -- and a
second copy of the scaffolding is a second thing to keep true.

Everything here goes through ``run_analysis_on_repo`` rather than calling the
layers directly. The defects these suites cover live in the handoff between the
incremental plan and the orchestrator, so a helper that drove the orchestrator
would step over exactly what is being tested.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any


def make_user(github_id: int, login: str) -> int:
    """A real owner, because every read of a run is scoped by one."""
    from archguard.db import store
    from archguard.db.session import session_scope

    async def _go() -> int:
        async with session_scope() as session:
            user = await store.upsert_user(session, github_id=github_id, login=login)
            return user.id

    return asyncio.run(_go())


def scan_repo(
    source: Path,
    workdir: Path,
    repo_url: str,
    user_id: int,
    previous: Any = None,
) -> dict[str, Any]:
    """One scan through the real pipeline; incremental when *previous* is given.

    Copies *source* into a fresh *workdir* first, because that is what the
    product does: every job clones the repository into a new temporary
    directory that is deleted afterwards. It matters more than it looks. The
    embedding cache lives at ``repo_root/.archguard-cache/embeddings.db``
    (``AnalysisOrchestrator.__init__``, and the dashboard does not override it),
    so a fresh clone means a fresh corpus holding only what *this* run embedded.
    Scanning one directory twice would leave a deleted file's vectors in the
    corpus -- an artefact of the test rather than the product.

    Returns ``{"run": <persisted run dict>, "hashes": <what this scan measured>}``.
    """
    from archguard.dashboard.pipeline_adapter import (
        IncrementalContext,
        _archguard_version,
        run_analysis_on_repo,
    )
    from archguard.db import store
    from archguard.db.session import session_scope

    # `.git` is kept: the product clones blobless but with full history
    # (dashboard/workspace.py documents why depth must never be added), and
    # contract generation reads co-change data from it. Dropping it here would
    # silently exercise the directory-name fallback instead.
    #
    # `.archguard-cache` is not: production clones fresh, so the embedding
    # database and audit trail start empty every job.
    shutil.copytree(
        source, workdir, ignore=shutil.ignore_patterns(".archguard-cache")
    )

    async def _go() -> dict[str, Any]:
        async with session_scope() as session:
            job_id = (await store.create_job(session, repo_url, user_id=user_id)).id

        measured: dict[str, dict[str, str]] = {}
        ctx = IncrementalContext(
            previous=previous,
            version=_archguard_version(),
            record_hashes=lambda h: measured.__setitem__("hashes", h),
        )
        await run_analysis_on_repo(
            repo_path=workdir,
            job_id=job_id,
            repo_url=repo_url,
            progress_callback=None,
            incremental=ctx,
        )
        async with session_scope() as session:
            run = await store.get_latest_run(session, job_id, user_id)
        return {"run": run or {}, "hashes": measured.get("hashes", {})}

    return asyncio.run(_go())


def previous_run(repo: Path, scan: dict[str, Any]) -> Any:
    """What the next scan is told about this one.

    The contract comes from the persisted run rather than from disk, because
    that is where the worker gets it (``_incremental_context`` reads
    ``last["contract"]``). It matters for a repository with no committed
    `.archguard.yml`: the contract that scan used was generated inside a clone
    that has since been deleted, so reading *repo* would find nothing and every
    comparison would report the contract as changed.
    """
    from archguard.cache.incremental import PreviousRun
    from archguard.dashboard.pipeline_adapter import (
        _archguard_version,
        _safe_load_contract,
    )

    stored = scan["run"].get("contract")
    return PreviousRun(
        contract=stored if stored else _safe_load_contract(repo),
        archguard_version=_archguard_version(),
        file_hashes=scan["hashes"],
        violations=list(scan["run"].get("violations") or []),
    )


def violations_of(run: dict[str, Any], layer: str | None = None) -> list[dict[str, Any]]:
    """Persisted violations, optionally for one layer.

    ``str`` on the layer because the value is an int inside the analyser and a
    string once it has been through the database.
    """
    found = list(run.get("violations") or [])
    if layer is None:
        return found
    return [v for v in found if str(v.get("layer")) == layer]


def identity(violations: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """What makes two persisted findings the same finding, order-independent."""
    return sorted(
        (
            str(v.get("layer") or ""),
            str(v.get("module") or ""),
            str(v.get("message") or ""),
        )
        for v in violations
    )
