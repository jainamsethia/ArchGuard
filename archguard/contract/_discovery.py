"""Repository discovery: scan, read co-change history, detect module communities.

Extracted verbatim (minus terminal I/O) from ``archguard.cli._init_phases`` so
the website no longer reaches into the CLI package to generate a contract. The
algorithms are unchanged; ``rich.Console`` printing is replaced by an optional
``on_progress`` callback plus module logging, so this runs identically under a
web request, a worker, or a test.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from archguard.utils.paths import SKIP_DIRS as _EXCLUDE_DIRS

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]

# How long to allow the history-extraction git process to run.
GIT_LOG_TIMEOUT_SECONDS: int = 120

# Only the most recent MAX_COMMIT_WINDOW commits are turned into co-change
# edges, and only when the repo has more than COMMIT_WINDOW_TRIGGER commits.
COMMIT_WINDOW_TRIGGER: int = 1000
MAX_COMMIT_WINDOW: int = 500

# A single commit touching N files contributes N*(N-1)/2 co-change edges.
# Bulk reformats / vendored-tree drops therefore swamp the graph with edges
# that carry no real coupling signal (and can exhaust memory on big repos),
# so commits above this width are counted but not turned into edges.
MAX_FILES_PER_COMMIT: int = 100

# Values for the ``history_status`` field returned by scan_commit_history.
HISTORY_OK = "ok"
HISTORY_UNAVAILABLE = "unavailable"


def _emit(on_progress: ProgressFn | None, message: str) -> None:
    logger.info("%s", message)
    if on_progress is not None:
        on_progress(message)


def _git_executable() -> str:
    """Return a usable git executable path, or raise if none is on PATH."""
    found = shutil.which("git")
    if not found:
        raise RuntimeError("git executable not found in PATH")
    return found


def count_loc(file_path: Path) -> int:
    """Count non-blank lines of code."""
    try:
        return sum(
            1
            for line in file_path.read_text(errors="replace").splitlines()
            if line.strip()
        )
    except OSError:
        return 0


def fallback_directory_modules(repo_path: Path) -> dict[str, list[str]]:
    """Detect modules from directory structure when commit history is sparse."""
    modules: dict[str, list[Path]] = {}
    for py_file in repo_path.rglob("*.py"):
        if any(part.startswith(".") for part in py_file.parts):
            continue  # Skip hidden dirs

        relative = py_file.relative_to(repo_path)
        parts = relative.parts

        if "test" in parts or "tests" in parts:
            modules.setdefault("tests", []).append(relative)
            continue

        # Use top-level package as module name
        if len(parts) >= 2:
            module_name = parts[0]
        else:
            module_name = "root"
        modules.setdefault(module_name, []).append(relative)

    # Merge tiny modules into 'misc'
    small_modules = [k for k, v in modules.items() if len(v) < 3]
    if small_modules:
        misc_files = []
        for k in small_modules:
            misc_files.extend(modules.pop(k))
        if misc_files:
            modules["misc"] = misc_files

    if not modules:
        return {
            "main": [
                str(p.relative_to(repo_path)).replace("\\", "/")
                for p in repo_path.rglob("*.py")
                if not any(part.startswith(".") for part in p.parts)
            ]
        }

    return {k: [str(p).replace("\\", "/") for p in v] for k, v in modules.items()}


def _name_for_path(path: str) -> str:
    """Derive a readable module name from an inferred contract path."""
    parts = [p for p in path.strip("/").split("/") if p and p != "."]
    if not parts:
        return "root"
    # "src/click/" reads better as "click" than "src"; only fall back to the
    # generic wrapper directory when it is the only component.
    if len(parts) > 1 and parts[0] in {"src", "lib"}:
        parts = parts[1:]
    return ".".join(parts)


def consolidate_communities(communities: dict[str, list[str]]) -> dict[str, list[str]]:
    """Merge co-change communities that resolve to the same contract path.

    A contract module is addressed by a single ``path`` prefix, but Louvain
    communities are groups of co-changing files that frequently share one.  When
    several do, the contract cannot tell them apart: ``_assign_file_to_module``
    resolves by longest-prefix match, so the first module with that path absorbs
    every file and its duplicates are scored as if they were clean.  Merging
    them produces one module per distinct boundary the analysis can actually
    measure, named after the directory it covers.
    """
    from archguard.contract.writer import _infer_path

    by_path: dict[str, list[str]] = {}
    for files in communities.values():
        if not files:
            continue
        by_path.setdefault(_infer_path(files), []).extend(files)

    merged: dict[str, list[str]] = {}
    for path, files in sorted(by_path.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        name = _name_for_path(path)
        if name in merged:  # distinct paths, colliding derived name
            name = path.strip("/").replace("/", ".") or "root"
        merged[name] = sorted(set(files))
    return merged


def scan_repository(repo_root: Path) -> dict[str, Any]:
    """Phase 1: enumerate analysable Python files and total LOC."""
    python_files: list[str] = []

    for py_file in sorted(repo_root.rglob("*.py")):
        if any(skip in py_file.parts for skip in _EXCLUDE_DIRS):
            continue
        try:
            rel = str(py_file.relative_to(repo_root)).replace("\\", "/")
            python_files.append(rel)
        except ValueError:
            continue

    total_loc = sum(count_loc(repo_root / f) for f in python_files)

    return {
        "total_files": len(python_files),
        "total_loc": total_loc,
        "python_files": python_files,
    }


def _assert_is_repo_root(repo_root: Path, git: str) -> None:
    """Fail unless *repo_root* is itself the root of a git repository.

    ``git -C DIR`` walks *up* the filesystem looking for a repository, so
    pointing it at a plain directory nested anywhere under one silently reports
    that ancestor's history instead.  A user's home directory being a git repo
    is enough to make an unrelated project's commits look like this project's.
    Refuse rather than analyse the wrong repository.
    """
    proc = subprocess.run(
        [git, "-C", str(repo_root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
        timeout=GIT_LOG_TIMEOUT_SECONDS,
    )
    toplevel = Path(proc.stdout.strip()).resolve()
    if os.path.normcase(str(toplevel)) != os.path.normcase(str(repo_root.resolve())):
        raise RuntimeError(
            f"{repo_root} is not a git repository root; git resolved it to "
            f"{toplevel}. Refusing to attribute that repository's history here."
        )


def _count_commits(repo_root: Path, git: str) -> int:
    """Total commits reachable from HEAD -- the honest depth-of-history figure."""
    proc = subprocess.run(
        [git, "-C", str(repo_root), "rev-list", "--count", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        timeout=GIT_LOG_TIMEOUT_SECONDS,
    )
    return int(proc.stdout.strip() or 0)


def _read_commit_file_sets(
    repo_root: Path, git: str, window: int | None
) -> list[list[str]]:
    """Return the changed ``.py`` paths for each non-merge commit, newest first.

    Deliberately uses ``git log --name-only`` rather than pydriller's
    ``commit.modified_files``.  Only the *names* of co-changed files are needed
    to build the co-change graph, and ``--name-only`` reads them from commit and
    tree objects alone.  ``modified_files`` computes full textual diffs, which
    needs every historical **blob**; on the partial (``--filter=blob:none``)
    clones the dashboard makes, that turns into one lazy network fetch per
    commit -- minutes of latency when the promisor remote is reachable, and a
    hard ``git diff-tree`` failure when it is not.

    Paths are returned repo-relative (``src/pkg/mod.py``), not basenames, so
    that downstream path inference and file reads can locate them.
    """
    cmd = [
        git,
        "-C",
        str(repo_root),
        "-c",
        "core.quotepath=false",  # emit UTF-8 paths, not octal escapes
        "log",
        "--no-merges",  # merges re-report their branch's changes as co-changes
        "--no-renames",
        # ^ Rename detection compares blob *contents* to score similarity, so
        # leaving it on (the default) reintroduces exactly the promisor-fetch
        # dependency this function exists to avoid: on a blobless clone git
        # aborts with "could not fetch <sha> from promisor remote".  Without it
        # a rename reads as delete+add, which is the correct signal for
        # co-change coupling anyway.
        "--name-only",
        "--format=%x00%H",  # NUL-delimit records; NUL cannot occur in a path
    ]
    if window is not None:
        cmd.append(f"-n{window}")
    cmd.extend(["--", "*.py"])

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=GIT_LOG_TIMEOUT_SECONDS,
    )

    file_sets: list[list[str]] = []
    for record in proc.stdout.split("\x00"):
        lines = record.splitlines()
        if not lines:
            continue
        # lines[0] is the commit sha; the rest are changed paths.
        paths = [ln.strip() for ln in lines[1:] if ln.strip().endswith(".py")]
        if paths:
            file_sets.append(paths)
    return file_sets


def scan_commit_history(repo_root: Path) -> dict[str, Any]:
    """Phase 2: build the co-change graph from git history.

    Returns ``history_status`` alongside the counts so callers can tell a repo
    with genuinely little history apart from one whose history could not be
    read -- previously both collapsed to ``commit_count == 0``.
    """
    import networkx as nx  # lazy import

    graph = nx.Graph()
    commit_count = 0
    commits_analyzed = 0
    wide_commits_skipped = 0
    history_status = HISTORY_OK
    history_error = ""

    try:
        git = _git_executable()
        _assert_is_repo_root(repo_root, git)
        commit_count = _count_commits(repo_root, git)
        window = MAX_COMMIT_WINDOW if commit_count > COMMIT_WINDOW_TRIGGER else None

        for py_files in _read_commit_file_sets(repo_root, git, window):
            if len(py_files) > MAX_FILES_PER_COMMIT:
                wide_commits_skipped += 1
                continue
            commits_analyzed += 1
            for i, f1 in enumerate(py_files):
                for f2 in py_files[i + 1 :]:
                    if graph.has_edge(f1, f2):
                        graph[f1][f2]["weight"] += 1
                    else:
                        graph.add_edge(f1, f2, weight=1)
    except Exception as exc:
        # Never silently degrade to "this repo has no history": that is
        # indistinguishable from a genuinely young repo and silently swings the
        # whole analysis onto the directory-name heuristic. Record *why*.
        history_status = HISTORY_UNAVAILABLE
        history_error = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            history_error += f" (git stderr: {str(exc.stderr).strip()[:500]})"
        logger.warning(
            "Git history extraction failed for %s: %s",
            repo_root,
            history_error,
            exc_info=True,
        )
        graph.clear()
        commits_analyzed = 0

    if wide_commits_skipped:
        logger.info(
            "Skipped %d commit(s) touching more than %d Python files when building "
            "the co-change graph.",
            wide_commits_skipped,
            MAX_FILES_PER_COMMIT,
        )

    return {
        "commit_count": commit_count,
        "commits_analyzed": commits_analyzed,
        "history_status": history_status,
        "history_error": history_error,
        "graph_nodes": graph.number_of_nodes(),
        "graph_edges": graph.number_of_edges(),
        "graph_data": nx.node_link_data(graph),
    }


def detect_module_communities(
    graph_data: dict[str, Any],
    repo_root: Path,
    python_files: list[str],
    commit_count: int,
    min_history: int,
    history_status: str = HISTORY_OK,
    history_error: str = "",
    on_progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Phase 3: Louvain community detection over the co-change graph.

    Falls back to the directory-name heuristic when the co-change graph cannot
    support real community detection.  ``fallback_reason`` records *which* of
    the distinct causes applied, so "we could not read this repo's history" is
    never reported as "this repo has little history".
    """
    import networkx as nx  # lazy import

    from archguard.analysis.community import detect_communities, get_seed_from_repo

    graph = nx.node_link_graph(graph_data)
    seed = get_seed_from_repo(repo_root)
    fallback_reason = ""

    if history_status != HISTORY_OK:
        fallback_reason = "history_unavailable"
        _emit(
            on_progress,
            "Commit history could not be read "
            f"({history_error or 'unknown error'}). "
            "Falling back to directory-structure-based module detection.",
        )
        communities = fallback_directory_modules(repo_root)
    elif commit_count < min_history:
        fallback_reason = "sparse_history"
        _emit(
            on_progress,
            f"Insufficient commit history ({commit_count} < {min_history}). "
            "Falling back to directory-structure-based module detection.",
        )
        communities = fallback_directory_modules(repo_root)
    else:
        communities = consolidate_communities(
            detect_communities(graph, seed=seed, min_community_size=2)
        )

        # Fallback: co-change graph too thin to separate modules.
        if not communities or len(communities) < 2:
            fallback_reason = "low_community_diversity"
            _emit(
                on_progress,
                "Insufficient community diversity detected in the co-change "
                "graph. Falling back to directory-structure-based module "
                "detection.",
            )
            communities = fallback_directory_modules(repo_root)

    return {
        "seed": seed,
        "num_communities": len(communities),
        "communities": communities,
        "coherence_warnings": [],
        # Derived from which branch actually ran, not re-inferred from the
        # post-fallback community count -- re-inferring reported False whenever
        # the heuristic happened to yield >= 2 modules, hiding the fallback.
        "fallback_used": bool(fallback_reason),
        "fallback_reason": fallback_reason,
    }
