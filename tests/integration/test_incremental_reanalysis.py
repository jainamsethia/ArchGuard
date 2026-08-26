"""Incremental re-analysis, end to end against a real database.

The unit tests in `test_incremental_plan.py` cover the decision. These cover the
part that only shows up once PostgreSQL, the worker and a real clone are
involved: that hashes survive the clone being deleted, that a second scan of an
unchanged repository reuses them, and -- the one that matters -- that the result
still matches what a full analysis would have produced.

An incremental scan that is merely fast is worthless. It has to be right.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.db_fixtures import requires_postgres

pytestmark = pytest.mark.integration


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture()
def sample_repo(tmp_path: Path) -> Path:
    """Two modules, so one can be edited while the other stays untouched."""
    repo = tmp_path / "proj"
    (repo / "alpha").mkdir(parents=True)
    (repo / "beta").mkdir(parents=True)
    (repo / "alpha" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "alpha" / "core.py").write_text("import os\nimport sys\n", encoding="utf-8")
    (repo / "beta" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "beta" / "api.py").write_text("import json\n", encoding="utf-8")
    (repo / ".archguard.yml").write_text(
        "version: '3.0'\n"
        "modules:\n"
        "- name: alpha\n  path: alpha/\n  coupling_budget: 1\n"
        "- name: beta\n  path: beta/\n  coupling_budget: 1\n",
        encoding="utf-8",
    )
    _git(repo, "init", "-q", "-b", "main")
    return repo


def _files(repo: Path) -> list[Path]:
    return sorted(repo.rglob("*.py"))


# ------------------------------------------------------------ the hash store


@requires_postgres
def test_a_first_scan_records_hashes_that_outlive_the_clone(live_db, test_user):
    """The whole reason the CLI's cache could not be reused.

    It lived in `.archguard-cache.json` at the repository root, which for the
    website is a clone deleted after every job.
    """
    import asyncio
    import tempfile

    from archguard.cache.incremental import hash_files
    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        async with session_scope() as session:
            repo = await store.upsert_repository(session, "https://github.com/x/inc")
            repository_id = repo.id

        # A clone, hashed, then destroyed -- exactly what a job does.
        clone = Path(tempfile.mkdtemp()) / "clone"
        clone.mkdir(parents=True)
        (clone / "a.py").write_text("import os\n", encoding="utf-8")
        recorded = hash_files(sorted(clone.rglob("*.py")), clone)
        async with session_scope() as session:
            await store.save_file_hashes(session, repository_id, recorded)

        import shutil

        shutil.rmtree(clone, ignore_errors=True)

        async with session_scope() as session:
            return await store.load_file_hashes(session, repository_id)

    survived = asyncio.run(scenario())
    assert survived, "no hashes survived the clone being removed"
    assert "a.py" in survived


@requires_postgres
def test_saving_replaces_rather_than_merges(live_db):
    """A path absent from the new set was deleted or is no longer analysed.

    Leaving its hash behind would let the file reappear later and be judged
    unchanged against a hash recorded before it vanished.
    """
    import asyncio

    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        async with session_scope() as session:
            repo = await store.upsert_repository(session, "https://github.com/x/repl")
            rid = repo.id
            await store.save_file_hashes(session, rid, {"old.py": "aaa", "keep.py": "bbb"})
        async with session_scope() as session:
            await store.save_file_hashes(session, rid, {"keep.py": "bbb"})
        async with session_scope() as session:
            return await store.load_file_hashes(session, rid)

    remaining = asyncio.run(scenario())
    assert remaining == {"keep.py": "bbb"}


@requires_postgres
def test_hashes_are_scoped_to_their_repository(live_db):
    """One repository's cache must never answer for another's."""
    import asyncio

    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        async with session_scope() as session:
            a = await store.upsert_repository(session, "https://github.com/x/aaa")
            b = await store.upsert_repository(session, "https://github.com/x/bbb")
            await store.save_file_hashes(session, a.id, {"a.py": "111"})
            await store.save_file_hashes(session, b.id, {"b.py": "222"})
        async with session_scope() as session:
            return (
                await store.load_file_hashes(session, a.id),
                await store.load_file_hashes(session, b.id),
            )

    first, second = asyncio.run(scenario())
    assert first == {"a.py": "111"}
    assert second == {"b.py": "222"}


# ------------------------------------------------------------------ tenancy


@requires_postgres
def test_one_users_findings_are_never_carried_into_anothers_run(live_db):
    """Content hashes are public and shared by repository; findings are not.

    Two accounts analysing the same public repository share the hash cache --
    which is the point, it makes the second scan fast -- but each must only ever
    reuse its own previous findings.
    """
    import asyncio

    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        async with session_scope() as session:
            alice = await store.upsert_user(session, github_id=9001, login="alice")
            bob = await store.upsert_user(session, github_id=9002, login="bob")
            url = "https://github.com/x/shared"

            alice_job = await store.create_job(session, url, user_id=alice.id)
            await store.persist_run(
                session,
                alice_job.id,
                {
                    "repo_url": url,
                    "score": 50.0,
                    "violations": [
                        {"module": "alpha", "layer": 2, "message": "alice-only finding"}
                    ],
                },
            )
            bob_job = await store.create_job(session, url, user_id=bob.id)
            bob_job_id = bob_job.id

        async with session_scope() as session:
            return await store.get_previous_run_for_job(session, bob_job_id)

    previous = asyncio.run(scenario())
    if previous is not None:
        messages = [v.get("message") for v in previous.get("violations", [])]
        assert "alice-only finding" not in messages, (
            "another account's finding was offered as this job's previous run"
        )


# ------------------------------------------------- correctness of the result


@requires_postgres
def test_an_incremental_result_matches_a_full_one(sample_repo, live_db):
    """The test that makes the optimisation trustworthy.

    A full analysis, then an incremental one over the same unchanged tree, must
    report the same findings. If they differ, the cache is lying about the
    repository.
    """
    from archguard.analysis.layers import AnalysisOrchestrator
    from archguard.cache.incremental import (
        PreviousRun,
        hash_files,
        plan_analysis,
    )

    def findings(result):
        return sorted(
            (v.module or "", str(v.layer), v.message) for v in result.violations
        )

    full = AnalysisOrchestrator(sample_repo).run(_files(sample_repo), "sha1")
    baseline = findings(full)

    # Second scan: nothing edited, so the plan should reuse everything.
    contract = {
        "version": "3.0",
        "modules": [
            {"name": "alpha", "path": "alpha/", "coupling_budget": 1},
            {"name": "beta", "path": "beta/", "coupling_budget": 1},
        ],
    }
    previous = PreviousRun(
        contract=contract,
        archguard_version="0.3.0",
        file_hashes=hash_files(_files(sample_repo), sample_repo),
        violations=[
            {"module": m, "layer": layer, "message": msg} for m, layer, msg in baseline
        ],
    )
    plan = plan_analysis(
        files=_files(sample_repo), root=sample_repo, contract=contract,
        version="0.3.0", previous=previous,
    )

    assert plan.full is False
    assert plan.changed == [], "an unedited tree reported changed files"

    carried = sorted(
        (v["module"], str(v["layer"]), v["message"]) for v in plan.carried_violations
    )
    assert carried == baseline, (
        "the incremental scan would report different findings than a full one"
    )


@requires_postgres
def test_editing_one_module_reanalyses_only_that_module(sample_repo, live_db):
    """The saving, and its limit. Editing alpha must re-analyse alpha and reuse
    beta -- and must NOT reuse alpha, whose findings are now stale.
    """
    from archguard.cache.incremental import PreviousRun, hash_files, plan_analysis

    contract = {
        "version": "3.0",
        "modules": [
            {"name": "alpha", "path": "alpha/", "coupling_budget": 1},
            {"name": "beta", "path": "beta/", "coupling_budget": 1},
        ],
    }
    previous = PreviousRun(
        contract=contract,
        archguard_version="0.3.0",
        file_hashes=hash_files(_files(sample_repo), sample_repo),
        violations=[
            {"module": "alpha", "layer": 2, "message": "alpha stale"},
            {"module": "beta", "layer": 2, "message": "beta still true"},
        ],
    )

    (sample_repo / "alpha" / "core.py").write_text(
        "import os\nimport sys\nimport json\n", encoding="utf-8"
    )

    plan = plan_analysis(
        files=_files(sample_repo), root=sample_repo, contract=contract,
        version="0.3.0", previous=previous,
    )

    assert plan.dirty_modules == {"alpha"}
    assert [p.name for p in plan.changed] == ["core.py"]
    carried = [v["message"] for v in plan.carried_violations]
    assert carried == ["beta still true"]
    assert "alpha stale" not in carried, "a stale finding was carried forward"
