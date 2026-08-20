"""Louvain community detection for co-change graphs."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def detect_communities(
    graph: Any,
    seed: int,
    min_community_size: int = 2,
) -> dict[str, list[str]]:
    """Run Louvain community detection on a co-change graph.

    Returns ``{community_id_str: [file_path, ...]}``.
    Communities smaller than *min_community_size* are filtered out.
    Community IDs are ``module_0``, ``module_1``, ... sorted by size descending.
    """
    import community as community_louvain  # python-louvain

    if graph.number_of_nodes() == 0:
        return {}

    partition: dict[str, int] = community_louvain.best_partition(
        graph,
        random_state=seed,
        weight="weight",
    )

    # Group files by community id
    groups: dict[int, list[str]] = {}
    for node, comm_id in partition.items():
        groups.setdefault(comm_id, []).append(node)

    # Sort communities by size descending
    sorted_groups = sorted(groups.values(), key=len, reverse=True)

    # Filter and rename
    result: dict[str, list[str]] = {}
    idx = 0
    for files in sorted_groups:
        if len(files) >= min_community_size:
            result[f"module_{idx}"] = sorted(files)
            idx += 1

    return result


def get_seed_from_repo(repo_root: Path) -> int:
    """Derive a deterministic seed from the repository's root commit SHA.

    Falls back to ``42`` if no commits are available.
    """
    try:
        from pydriller import Repository  # lazy import

        repo = Repository(str(repo_root))
        commits = list(repo.traverse_commits())
        if commits:
            return int(commits[0].hash[:8], 16)
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            f"Non-critical failure in get_seed_from_repo: {e}"
        )
    return 42
