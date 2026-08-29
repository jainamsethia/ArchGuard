"""Deciding what a re-scan has to redo, and what it can reuse.

The CLI kept SHA-256 content hashes in `.archguard-cache.json` at the analysed
repository's root. For the website that root is a clone made per job and deleted
afterwards, so the cache could never survive long enough to be worth anything.
The hashing and the changed/unchanged partitioning were always sound; only the
place they were stored was wrong.

So the storage is a protocol now, backed by PostgreSQL and keyed by repository,
and everything here is pure: it takes the files on disk, the hashes recorded
last time and what the previous run was measured with, and returns a decision.
That matters because the decision is where correctness lives. Reusing a finding
that should have been recomputed reports a repository as clean when it is not,
which is a worse failure than any amount of redundant work.

Every uncertainty therefore resolves toward doing more work: an unreadable file
counts as changed, an unrecognised module is not carried forward, and a changed
contract or ArchGuard version discards the cache entirely.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class FileHashStore(Protocol):
    """Where content hashes live between scans.

    A protocol rather than a class so the analysis has no opinion about
    storage: the website backs it with PostgreSQL, and a test backs it with a
    dictionary.
    """

    def load(self, repository_id: int) -> dict[str, str]:
        """Recorded hashes for a repository, keyed by repo-relative path."""
        ...

    def save(self, repository_id: int, records: dict[str, str]) -> None:
        """Replace the recorded hashes for a repository."""
        ...


def compute_hash(file_path: Path) -> str:
    """SHA-256 of a file's bytes.

    Content, not mtime: a fresh clone rewrites every timestamp without changing
    a byte, and an mtime cache would call the entire repository changed on
    every single scan -- which is the same as having no cache.
    """
    digest = hashlib.sha256()
    digest.update(file_path.read_bytes())
    return digest.hexdigest()


def _key(path: Path, root: Path) -> str:
    """Repo-relative, forward-slashed, so a hash recorded on one host matches
    the same file on another."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    return str(relative).replace("\\", "/")


def hash_files(files: list[Path], root: Path) -> dict[str, str]:
    """Hash every file, skipping any that cannot be read."""
    recorded: dict[str, str] = {}
    for path in files:
        try:
            recorded[_key(path, root)] = compute_hash(path)
        except OSError as exc:
            logger.warning("Could not hash %s: %s", path, exc)
    return recorded


def partition_changed(
    files: list[Path], root: Path, known: dict[str, str]
) -> tuple[list[Path], list[Path]]:
    """Split files into (changed, unchanged) against the recorded hashes.

    A file that cannot be read is reported changed. It might have been edited,
    and assuming otherwise is how a stale finding outlives the code that
    produced it.

    Deletions need no handling: a path no longer on disk is not in `files`, so
    it cannot be reported unchanged.
    """
    changed: list[Path] = []
    unchanged: list[Path] = []
    for path in files:
        key = _key(path, root)
        try:
            current = compute_hash(path)
        except OSError as exc:
            logger.warning("Could not hash %s (%s); treating as changed", path, exc)
            changed.append(path)
            continue
        if known.get(key) == current:
            unchanged.append(path)
        else:
            changed.append(path)
    return changed, unchanged


def dirty_modules(
    changed: list[Path], root: Path, module_paths: dict[str, list[str]]
) -> set[str]:
    """Modules containing at least one changed file.

    Whole modules, not files: Layers 2 and 3 measure a module as a unit -- fan-
    out is a property of everything the module imports, drift a property of
    everything it contains -- so one edited file makes the module's previous
    findings unusable even for the files that did not change.
    """
    from archguard.utils.paths import path_belongs_to_module

    dirty: set[str] = set()
    for path in changed:
        key = _key(path, root)
        for name, paths in module_paths.items():
            if path_belongs_to_module(key, paths):
                dirty.add(name)
    return dirty


def contract_fingerprint(contract: dict[str, Any]) -> str:
    """A stable digest of a contract's meaning.

    Sorted keys, so re-serialising the same contract does not read as a change
    and force a full analysis for nothing.
    """
    return hashlib.sha256(
        json.dumps(contract, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


#: Layers whose findings describe more than the module they are filed under.
#:
#: Layers 1-3 are attributable: an import-boundary violation belongs to a file,
#: a fan-out or drift figure to a module. Skipping an unchanged module and
#: carrying its previous findings forward is therefore sound -- nothing outside
#: it can make them false.
#:
#: A Layer 4 duplication finding is a *relationship*. Its message names a clone
#: spanning two files ("a.py <-> b.py") but the row is recorded against one
#: module, so it stops being true when the OTHER module changes -- and this
#: plan cannot tell which one that was. Such a finding is never carried
#: forward; its layer is re-measured over `AnalysisPlan.duplication_files`
#: instead. Add a layer number here rather than special-casing it elsewhere.
RELATIONAL_LAYERS = frozenset({"4"})


@dataclass(frozen=True)
class PreviousRun:
    """What the last analysis of this repository was measured with."""

    contract: dict[str, Any]
    archguard_version: str
    file_hashes: dict[str, str]
    violations: list[dict[str, Any]]


@dataclass(frozen=True)
class AnalysisPlan:
    """What this scan must do.

    `full` is the safe answer and the default for anything unexpected. When it
    is True the other fields describe a complete analysis and nothing is
    carried forward.
    """

    full: bool
    reason: str
    changed: list[Path]
    unchanged: list[Path]
    dirty_modules: set[str] = field(default_factory=set)
    carried_violations: list[dict[str, Any]] = field(default_factory=list)

    #: Every file this scan looked at, changed or not.
    #:
    #: Layers in RELATIONAL_LAYERS must see all of it. A clone's counterpart
    #: commonly lives in a file that did not change, and a Layer 4 run scoped
    #: to `changed` alone can neither find it nor clear a stale finding about
    #: it -- it simply has nothing to compare against.
    duplication_files: list[Path] = field(default_factory=list)


def plan_analysis(
    *,
    files: list[Path],
    root: Path,
    contract: dict[str, Any],
    version: str,
    previous: PreviousRun | None,
) -> AnalysisPlan:
    """Decide what to re-analyse, and which previous findings still hold.

    Three things discard the cache outright, because each makes every previous
    finding unsafe to reuse:

    * no previous run -- there is nothing to reuse;
    * a changed contract -- thresholds and module boundaries decide what counts
      as a violation, so the old findings were measured against rules that no
      longer apply;
    * a changed ArchGuard version -- a newer analyser may detect things the old
      one could not, and carrying forward its findings would hide exactly the
      new detections.
    """
    module_paths = {
        m["name"]: _module_paths(m) for m in contract.get("modules", []) if m.get("name")
    }

    def everything(reason: str) -> AnalysisPlan:
        return AnalysisPlan(
            full=True,
            reason=reason,
            changed=list(files),
            unchanged=[],
            duplication_files=list(files),
        )

    if previous is None:
        return everything("no previous run for this repository")
    if contract_fingerprint(previous.contract) != contract_fingerprint(contract):
        return everything("the contract changed since the last run")
    if (previous.archguard_version or "") != version:
        return everything(
            "the ArchGuard version changed from "
            f"{previous.archguard_version or 'unknown'} to {version}"
        )

    changed, unchanged = partition_changed(files, root, previous.file_hashes)
    dirty = dirty_modules(changed, root, module_paths)

    # Only findings belonging to a module that this scan will not re-analyse.
    # A finding with no module cannot be attributed to one, and a module the
    # contract no longer declares was measured against a boundary that is gone;
    # both are left for this run to produce again if they still hold.
    #
    # And never a relational one, however clean its own module looks. See
    # RELATIONAL_LAYERS: a duplication finding depends on a module this plan
    # may have decided was untouched, so "my module is clean" is not evidence
    # that it still holds. `str` because the value is an int coming out of the
    # analyser and a string coming back out of the database.
    carried = [
        v
        for v in previous.violations
        if v.get("module")
        and v["module"] in module_paths
        and v["module"] not in dirty
        and str(v.get("layer")) not in RELATIONAL_LAYERS
    ]

    return AnalysisPlan(
        full=False,
        reason=(
            f"{len(changed)} changed, {len(unchanged)} unchanged; "
            f"{len(dirty)} module(s) to re-analyse"
        ),
        changed=changed,
        unchanged=unchanged,
        dirty_modules=dirty,
        carried_violations=carried,
        # changed + unchanged is the whole input set by construction.
        duplication_files=list(files),
    )


def _module_paths(module: dict[str, Any]) -> list[str]:
    """A contract module's paths, however it spells them."""
    if module.get("paths"):
        return [str(p) for p in module["paths"]]
    if module.get("path"):
        return [str(module["path"])]
    return []
