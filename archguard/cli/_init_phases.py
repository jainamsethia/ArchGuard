from __future__ import annotations
from pathlib import Path
from typing import Any
from rich.console import Console

_console: Console = Console()

_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".venv",
        "venv",
        ".git",
        "node_modules",
        ".tox",
        "dist",
        "build",
    }
)


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


def _fallback_directory_modules(repo_path: Path) -> dict[str, list[str]]:
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


def _phase1_scan(repo_root: Path) -> dict[str, Any]:
    """Phase 1: Repository Scan."""
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


def _phase2_commits(repo_root: Path) -> dict[str, Any]:
    """Phase 2: Commit History Analysis."""
    from pydriller import Repository  # lazy import
    import networkx as nx  # lazy import

    try:
        repo = Repository(str(repo_root))
        commits = list(repo.traverse_commits())
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(f"Git history extraction failed: {e}")
        commits = []

    if len(commits) > 1000:
        commits = commits[-500:]

    graph = nx.Graph()

    for commit in commits:
        py_files = [
            m.filename
            for m in commit.modified_files
            if m.filename and m.filename.endswith(".py")
        ]
        for i, f1 in enumerate(py_files):
            for f2 in py_files[i + 1 :]:
                if graph.has_edge(f1, f2):
                    graph[f1][f2]["weight"] += 1
                else:
                    graph.add_edge(f1, f2, weight=1)

    return {
        "commit_count": len(commits),
        "graph_nodes": graph.number_of_nodes(),
        "graph_edges": graph.number_of_edges(),
        "graph_data": nx.node_link_data(graph),
    }


def _phase3_communities(
    graph_data: dict[str, Any],
    repo_root: Path,
    python_files: list[str],
    commit_count: int,
    min_history: int,
) -> dict[str, Any]:
    """Phase 3: Louvain Community Detection."""
    import networkx as nx  # lazy import
    from archguard.analysis.community import detect_communities, get_seed_from_repo

    graph = nx.node_link_graph(graph_data)
    seed = get_seed_from_repo(repo_root)

    if commit_count < min_history:
        _console.print(
            f"\n[yellow]⚠ Insufficient commit history ({commit_count} < {min_history}). "
            "Falling back to directory-structure-based module detection.[/yellow]"
        )
        communities = _fallback_directory_modules(repo_root)
    else:
        communities = detect_communities(graph, seed=seed, min_community_size=2)

        # Fallback: if no communities detected, create a single module
        if not communities or len(communities) < 2:
            _console.print(
                "\n[yellow]⚠ Insufficient community diversity detected in co-change graph. "
                "Falling back to directory-structure-based module detection.[/yellow]"
            )
            communities = _fallback_directory_modules(repo_root)

    return {
        "seed": seed,
        "num_communities": len(communities),
        "communities": communities,
        "coherence_warnings": [],
    }
