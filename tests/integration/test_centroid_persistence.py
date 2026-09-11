"""Layer 3's baseline has to outlive the clone it was measured in.

Semantic drift is defined against a centroid a previous scan recorded. That
centroid was written into a SQLite cache inside the analysed repository, and the
hosted path analyses a throwaway clone that is deleted when the job finishes --
so the baseline was gone before anything could compare against it, and Layer 3
reported "no prior baseline" on every run however many times a repository had
been scanned. It is the same problem `file_hashes` already solved, and it is
solved the same way: keyed by repository, in PostgreSQL.

The tests below cover the store, the two halves of the bridge between the
database and the clone's cache, and the behaviour that actually matters --
a second scan of the same repository measuring drift instead of skipping.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from tests.db_fixtures import requires_postgres
from tests.integration._pipeline_scan import requires_ml

pytestmark = pytest.mark.integration


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def repository(live_db):
    """A user and a repository row to hang centroids off."""
    from archguard.db import store
    from archguard.db.session import session_scope

    async def _go() -> int:
        async with session_scope() as session:
            await store.upsert_user(session, github_id=7710, login="centroid")
            repo = await store.upsert_repository(
                session, "https://github.com/test/centroids"
            )
            return repo.id

    return _run(_go())


# --------------------------------------------------------------- the store


@requires_postgres
def test_a_stored_centroid_comes_back_unchanged(repository):
    """Raw float32 bytes, returned byte for byte. The vector is read straight
    back into numpy by the seeding half, so any re-encoding here would be a
    silent corruption of the baseline."""
    from archguard.db import store
    from archguard.db.session import session_scope

    vector = bytes(range(16)) * 4  # 64 bytes, arbitrary but exact

    async def scenario():
        async with session_scope() as session:
            await store.save_module_centroids(
                session, repository, {"core": (vector, "abc123")}
            )
        async with session_scope() as session:
            return await store.load_module_centroids(session, repository)

    loaded = _run(scenario())
    assert loaded == {"core": (vector, "abc123")}


@requires_postgres
def test_saving_nothing_does_not_destroy_the_baseline(repository):
    """Layer 3 skips whenever the ML extras are absent, and a run that measured
    no centroid must not delete the one an earlier run established."""
    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        async with session_scope() as session:
            await store.save_module_centroids(
                session, repository, {"core": (b"\x01\x02\x03\x04", "h1")}
            )
        async with session_scope() as session:
            await store.save_module_centroids(session, repository, {})
        async with session_scope() as session:
            return await store.load_module_centroids(session, repository)

    assert _run(scenario()) == {"core": (b"\x01\x02\x03\x04", "h1")}


@requires_postgres
def test_a_module_that_no_longer_exists_is_dropped(repository):
    """Replace, not merge. A module absent from the new contract must not keep
    a baseline, or a later decomposition that reuses the name would be compared
    against a centroid measured from different files."""
    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        async with session_scope() as session:
            await store.save_module_centroids(
                session, repository,
                {"old": (b"\x00" * 8, "h1"), "kept": (b"\x01" * 8, "h2")},
            )
        async with session_scope() as session:
            await store.save_module_centroids(
                session, repository, {"kept": (b"\x02" * 8, "h3")}
            )
        async with session_scope() as session:
            return await store.load_module_centroids(session, repository)

    loaded = _run(scenario())
    assert set(loaded) == {"kept"}
    assert loaded["kept"] == (b"\x02" * 8, "h3")


@requires_postgres
def test_retention_drops_centroids_once_the_runs_are_gone(repository):
    """Centroids are the other half of the per-repository cache the hashes live
    in, and a baseline for a repository with no runs left is history nobody can
    see."""
    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        async with session_scope() as session:
            await store.save_module_centroids(
                session, repository, {"core": (b"\x09" * 8, "h")}
            )
        async with session_scope() as session:
            removed = await store.purge_expired_runs(
                session, retain_days=0, limit=100
            )
        async with session_scope() as session:
            return removed, await store.load_module_centroids(session, repository)

    removed, remaining = _run(scenario())
    assert remaining == {}
    assert removed["module_centroids"] >= 1


# ------------------------------------------------- the bridge to the clone


def test_seeding_survives_a_corrupt_row(tmp_path):
    """One unreadable baseline must cost that module its drift measurement and
    nothing else. Failing the analysis over a cache would be the wrong trade."""
    from archguard.dashboard.pipeline_adapter import _seed_centroids

    stored: list[str] = []

    class _Cache:
        def store_centroid(self, name, vector, content_hash):
            stored.append(name)

    class _Orchestrator:
        cache = _Cache()

    _seed_centroids(
        _Orchestrator(),
        {
            "good": ((b"\x00\x00\x80\x3f" * 4), "h1"),   # 4 float32 values
            "empty": (b"", "h2"),                        # nothing to restore
        },
    )
    assert stored == ["good"]


def test_seeding_nothing_is_a_no_op():
    from archguard.dashboard.pipeline_adapter import _seed_centroids

    class _Boom:
        @property
        def cache(self):  # pragma: no cover - must never be reached
            raise AssertionError("cache touched for an empty baseline")

    _seed_centroids(_Boom(), None)
    _seed_centroids(_Boom(), {})


# ------------------------------------------------------ what it is all for


@requires_postgres
@requires_ml
def test_a_second_scan_measures_drift_instead_of_skipping(tmp_path, live_db):
    """The behaviour the whole change exists for.

    Two analyses of the same repository in two different clones, with the
    centroids carried between them the way the worker carries them. The first
    has no baseline and says so; the second has one and measures against it.
    """
    import uuid

    from archguard.dashboard.pipeline_adapter import (
        IncrementalContext,
        run_analysis_on_repo,
    )

    source = tmp_path / "src"
    (source / "core").mkdir(parents=True)
    (source / "core" / "__init__.py").write_text("", encoding="utf-8")
    (source / "core" / "app.py").write_text(
        '"""Core."""\n\n\ndef total(values):\n'
        '    """Add up the values."""\n'
        "    running = 0\n"
        "    for value in values:\n"
        "        running = running + value\n"
        "    return running\n",
        encoding="utf-8",
    )
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", "t@example.com"),
        ("config", "user.name", "T"),
        ("add", "-A"),
        ("commit", "-q", "-m", "initial"),
    ):
        subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True)

    def _layer3(result):
        for lr in result.layer_results:
            d = lr if isinstance(lr, dict) else lr.__dict__
            if d.get("layer") == 3:
                return d
        raise AssertionError("no Layer 3 result")

    def _clone(dest: Path) -> Path:
        subprocess.run(
            ["git", "clone", "-q", str(source), str(dest)],
            check=True, capture_output=True,
        )
        return dest

    async def scenario():
        first_dir = _clone(tmp_path / "clone-one")
        ctx1 = IncrementalContext(previous=None, version="test",
                                  record_hashes=lambda _h: None)
        first = await run_analysis_on_repo(
            first_dir, str(uuid.uuid4()), "https://github.com/test/drift",
            incremental=ctx1,
        )

        # A fresh clone, exactly as the worker gets for every job.
        second_dir = _clone(tmp_path / "clone-two")
        ctx2 = IncrementalContext(previous=None, version="test",
                                  record_hashes=lambda _h: None,
                                  centroids=ctx1.measured_centroids)
        second = await run_analysis_on_repo(
            second_dir, str(uuid.uuid4()), "https://github.com/test/drift",
            incremental=ctx2,
        )
        return first, second, ctx1.measured_centroids

    first, second, carried = _run(scenario())

    assert carried, "the first scan recorded no centroid to carry forward"
    assert _layer3(first)["skipped"] is True, "a first scan has no baseline"
    assert "no prior baseline" in _layer3(first)["skip_reason"]
    assert _layer3(second)["skipped"] is False, (
        "the second scan still had no baseline, so the centroid did not survive "
        "the clone -- which is the whole point of persisting it"
    )
