"""Deciding what a re-scan actually has to re-analyse.

The pieces here are pure: given the files on disk, the hashes recorded last
time, and what the previous run was analysed with, decide whether this scan can
reuse anything and what it must redo.

Kept separate from the database and the orchestrator on purpose. The decision is
where correctness lives -- reusing a finding that should have been recomputed
reports a repository as clean when it is not -- and a decision that needs a
clone, a session and a worker to exercise is a decision nobody tests properly.

The bias throughout is toward doing too much work rather than too little. Every
uncertainty resolves to a full analysis.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from archguard.cache import incremental


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("import os\n", encoding="utf-8")
    (root / "pkg" / "b.py").write_text("import sys\n", encoding="utf-8")
    (root / "other").mkdir()
    (root / "other" / "c.py").write_text("x = 1\n", encoding="utf-8")
    return root


def files_of(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


# ------------------------------------------------------------------ hashing


def test_the_hash_follows_the_content(repo):
    first = incremental.compute_hash(repo / "pkg" / "a.py")
    (repo / "pkg" / "a.py").write_text("import os\nimport sys\n", encoding="utf-8")
    assert incremental.compute_hash(repo / "pkg" / "a.py") != first


def test_identical_content_hashes_identically(tmp_path):
    """Content, not mtime: a checkout rewrites timestamps without changing a
    byte, and re-analysing a whole repository for that is the bug this avoids.
    """
    (tmp_path / "x.py").write_text("same\n", encoding="utf-8")
    (tmp_path / "y.py").write_text("same\n", encoding="utf-8")
    assert incremental.compute_hash(tmp_path / "x.py") == incremental.compute_hash(
        tmp_path / "y.py"
    )


def test_hashes_are_keyed_by_repo_relative_posix_path(repo):
    recorded = incremental.hash_files(files_of(repo), repo)
    assert "pkg/a.py" in recorded, sorted(recorded)
    assert not any("\\" in k for k in recorded), "keys must be portable across hosts"


# --------------------------------------------------------------- partitioning


def test_everything_is_changed_when_nothing_is_known(repo):
    changed, unchanged = incremental.partition_changed(files_of(repo), repo, {})
    assert len(changed) == 3
    assert unchanged == []


def test_nothing_is_changed_when_everything_matches(repo):
    known = incremental.hash_files(files_of(repo), repo)
    changed, unchanged = incremental.partition_changed(files_of(repo), repo, known)
    assert changed == []
    assert len(unchanged) == 3


def test_only_the_edited_file_is_changed(repo):
    known = incremental.hash_files(files_of(repo), repo)
    (repo / "pkg" / "a.py").write_text("import os\nimport json\n", encoding="utf-8")

    changed, unchanged = incremental.partition_changed(files_of(repo), repo, known)

    assert [p.name for p in changed] == ["a.py"]
    assert sorted(p.name for p in unchanged) == ["b.py", "c.py"]


def test_a_new_file_counts_as_changed(repo):
    known = incremental.hash_files(files_of(repo), repo)
    (repo / "pkg" / "new.py").write_text("import os\n", encoding="utf-8")

    changed, _ = incremental.partition_changed(files_of(repo), repo, known)
    assert [p.name for p in changed] == ["new.py"]


def test_a_deleted_file_is_simply_absent(repo):
    """Deletion is handled by the file list, not the hash map: a path that is
    no longer on disk is not passed in, so it cannot be reported unchanged."""
    known = incremental.hash_files(files_of(repo), repo)
    (repo / "pkg" / "b.py").unlink()

    changed, unchanged = incremental.partition_changed(files_of(repo), repo, known)
    names = {p.name for p in changed + unchanged}
    assert "b.py" not in names


def test_an_unreadable_file_is_treated_as_changed(repo, monkeypatch):
    """Fail toward more work. A file we cannot hash might have changed, and
    assuming it did not is how a stale finding survives.
    """
    def boom(path):
        if path.name == "a.py":
            raise OSError("permission denied")
        return "deadbeef"

    monkeypatch.setattr(incremental, "compute_hash", boom)
    known = {"pkg/a.py": "deadbeef", "pkg/b.py": "deadbeef", "other/c.py": "deadbeef"}

    changed, _ = incremental.partition_changed(files_of(repo), repo, known)
    assert "a.py" in {p.name for p in changed}


# ------------------------------------------------------------ dirty modules


def test_a_module_is_dirty_when_any_of_its_files_changed(repo):
    """Layers 2 and 3 measure a module as a whole, so one edited file makes the
    whole module's previous findings unusable."""
    module_paths = {"pkg": ["pkg/"], "other": ["other/"]}
    changed = [repo / "pkg" / "a.py"]

    dirty = incremental.dirty_modules(changed, repo, module_paths)
    assert dirty == {"pkg"}


def test_a_changed_file_in_no_module_dirties_nothing(repo):
    module_paths = {"pkg": ["pkg/"]}
    dirty = incremental.dirty_modules([repo / "other" / "c.py"], repo, module_paths)
    assert dirty == set()


def test_every_module_is_dirty_when_everything_changed(repo):
    module_paths = {"pkg": ["pkg/"], "other": ["other/"]}
    dirty = incremental.dirty_modules(files_of(repo), repo, module_paths)
    assert dirty == {"pkg", "other"}


# ------------------------------------------------------------------- the plan


CONTRACT = {"version": "3.0", "modules": [{"name": "pkg", "path": "pkg/"}]}


def _previous(contract=None, version="0.3.0", hashes=None, violations=None):
    return incremental.PreviousRun(
        contract=contract if contract is not None else CONTRACT,
        archguard_version=version,
        file_hashes=hashes or {},
        violations=violations or [],
    )


def test_a_first_scan_is_a_full_analysis(repo):
    plan = incremental.plan_analysis(
        files=files_of(repo), root=repo, contract=CONTRACT,
        version="0.3.0", previous=None,
    )
    assert plan.full is True
    assert "no previous" in plan.reason.lower()
    assert len(plan.changed) == 3


def test_an_unchanged_rescan_reuses_everything(repo):
    known = incremental.hash_files(files_of(repo), repo)
    plan = incremental.plan_analysis(
        files=files_of(repo), root=repo, contract=CONTRACT, version="0.3.0",
        previous=_previous(hashes=known),
    )
    assert plan.full is False
    assert plan.changed == []
    assert len(plan.unchanged) == 3


def test_a_changed_contract_forces_a_full_analysis(repo):
    """Thresholds and module boundaries decide what counts as a violation, so
    every previous finding was measured against rules that no longer apply.
    """
    known = incremental.hash_files(files_of(repo), repo)
    changed_contract = {
        "version": "3.0",
        "modules": [{"name": "pkg", "path": "pkg/", "coupling_budget": 99}],
    }
    plan = incremental.plan_analysis(
        files=files_of(repo), root=repo, contract=changed_contract, version="0.3.0",
        previous=_previous(contract=CONTRACT, hashes=known),
    )
    assert plan.full is True
    assert "contract" in plan.reason.lower()


def test_a_changed_archguard_version_forces_a_full_analysis(repo):
    """A new version can detect things the old one did not, and carrying
    forward its findings would hide exactly the new detections."""
    known = incremental.hash_files(files_of(repo), repo)
    plan = incremental.plan_analysis(
        files=files_of(repo), root=repo, contract=CONTRACT, version="0.4.0",
        previous=_previous(version="0.3.0", hashes=known),
    )
    assert plan.full is True
    assert "version" in plan.reason.lower()


def test_an_unchanged_contract_with_reordered_keys_is_not_a_change(repo):
    """The fingerprint must be of meaning, not of dict ordering, or every scan
    is a full one for no reason."""
    known = incremental.hash_files(files_of(repo), repo)
    reordered = {"modules": [{"path": "pkg/", "name": "pkg"}], "version": "3.0"}
    plan = incremental.plan_analysis(
        files=files_of(repo), root=repo, contract=reordered, version="0.3.0",
        previous=_previous(contract=CONTRACT, hashes=known),
    )
    assert plan.full is False, plan.reason


def test_findings_are_carried_forward_only_for_clean_modules(repo):
    """The heart of it. A module nobody re-analysed still has whatever was
    wrong with it, and dropping its findings reports it as clean.
    """
    known = incremental.hash_files(files_of(repo), repo)
    (repo / "pkg" / "a.py").write_text("import os\nimport json\n", encoding="utf-8")

    previous_violations = [
        {"module": "pkg", "layer": 2, "message": "fan_out=9 exceeds budget=3"},
        {"module": "other", "layer": 2, "message": "fan_out=5 exceeds budget=3"},
    ]
    contract = {
        "version": "3.0",
        "modules": [{"name": "pkg", "path": "pkg/"}, {"name": "other", "path": "other/"}],
    }
    plan = incremental.plan_analysis(
        files=files_of(repo), root=repo, contract=contract, version="0.3.0",
        previous=_previous(contract=contract, hashes=known, violations=previous_violations),
    )

    assert plan.full is False
    assert plan.dirty_modules == {"pkg"}
    carried = [v["module"] for v in plan.carried_violations]
    assert carried == ["other"], "a dirty module's stale finding was carried forward"


def test_a_finding_with_no_module_is_never_carried(repo):
    """It cannot be attributed to something that was or was not re-analysed, so
    the only safe thing is to let this run produce it again."""
    known = incremental.hash_files(files_of(repo), repo)
    plan = incremental.plan_analysis(
        files=files_of(repo), root=repo, contract=CONTRACT, version="0.3.0",
        previous=_previous(hashes=known, violations=[{"layer": 4, "message": "clone"}]),
    )
    assert plan.carried_violations == []


def test_nothing_is_carried_forward_on_a_full_analysis(repo):
    """A full run recomputes everything; adding old findings would double them."""
    plan = incremental.plan_analysis(
        files=files_of(repo), root=repo, contract=CONTRACT, version="0.3.0",
        previous=None,
    )
    assert plan.carried_violations == []


def test_a_module_that_vanished_from_the_contract_is_not_carried(repo):
    """Its findings were measured against a boundary that no longer exists."""
    known = incremental.hash_files(files_of(repo), repo)
    plan = incremental.plan_analysis(
        files=files_of(repo), root=repo, contract=CONTRACT, version="0.3.0",
        previous=_previous(
            hashes=known,
            violations=[{"module": "deleted-module", "layer": 2, "message": "x"}],
        ),
    )
    assert plan.carried_violations == []
