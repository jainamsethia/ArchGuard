"""Unit tests for archguard.analysis.community."""

from __future__ import annotations

import networkx as nx
import pytest

from archguard.analysis.community import detect_communities


class TestDetectCommunities:
    """Tests for detect_communities()."""

    @pytest.fixture()
    def two_cluster_graph(self) -> nx.Graph:
        """Graph with 2 clear clusters connected by a weak link."""
        g = nx.Graph()
        # Cluster 1: a-b-c strongly connected
        g.add_edge("a.py", "b.py", weight=10)
        g.add_edge("b.py", "c.py", weight=10)
        g.add_edge("a.py", "c.py", weight=10)
        # Cluster 2: d-e-f strongly connected
        g.add_edge("d.py", "e.py", weight=10)
        g.add_edge("e.py", "f.py", weight=10)
        g.add_edge("d.py", "f.py", weight=10)
        # Weak inter-cluster link
        g.add_edge("c.py", "d.py", weight=1)
        return g

    def test_two_clusters_detected(self, two_cluster_graph: nx.Graph) -> None:
        """Graph with 2 clear clusters -> 2 communities detected."""
        result = detect_communities(two_cluster_graph, seed=42)
        assert len(result) == 2

    def test_communities_sorted_by_size_desc(
        self,
        two_cluster_graph: nx.Graph,
    ) -> None:
        """module_0 should be the largest community."""
        result = detect_communities(two_cluster_graph, seed=42)
        sizes = [len(files) for files in result.values()]
        assert sizes == sorted(sizes, reverse=True)

    def test_determinism_same_seed(
        self,
        two_cluster_graph: nx.Graph,
    ) -> None:
        """Same graph + same seed -> identical partition."""
        r1 = detect_communities(two_cluster_graph, seed=42)
        r2 = detect_communities(two_cluster_graph, seed=42)
        assert r1 == r2

    def test_seed_matters(self, two_cluster_graph: nx.Graph) -> None:
        """Same graph + different seed -> may produce different results.

        We just verify the function accepts different seeds without error.
        The partition may or may not differ for small graphs.
        """
        r1 = detect_communities(two_cluster_graph, seed=42)
        r2 = detect_communities(two_cluster_graph, seed=99)
        # Both should be valid community dicts
        assert isinstance(r1, dict)
        assert isinstance(r2, dict)

    def test_min_community_size_filters(self) -> None:
        """Community with 1 file filtered out when min_community_size=2."""
        g = nx.Graph()
        # Strong cluster
        g.add_edge("a.py", "b.py", weight=10)
        g.add_edge("b.py", "c.py", weight=10)
        g.add_edge("a.py", "c.py", weight=10)
        # Isolated node
        g.add_node("lonely.py")

        result = detect_communities(g, seed=42, min_community_size=2)
        # lonely.py should be filtered out
        all_files = [f for files in result.values() for f in files]
        assert "lonely.py" not in all_files

    def test_empty_graph(self) -> None:
        """Empty graph -> empty result."""
        g = nx.Graph()
        result = detect_communities(g, seed=42)
        assert result == {}


class TestGetSeedFromRepo:
    def test_get_seed_from_repo_success(
        self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from archguard.analysis.community import get_seed_from_repo
        from unittest.mock import MagicMock
        import sys

        mock_pydriller = MagicMock()
        mock_commit = MagicMock()
        mock_commit.hash = "01234567"
        mock_pydriller.Repository.return_value.traverse_commits.return_value = [
            mock_commit
        ]

        with monkeypatch.context() as m:
            m.setitem(sys.modules, "pydriller", mock_pydriller)
            assert get_seed_from_repo(tmp_path) == int("01234567", 16)

    def test_get_seed_from_repo_fallback(
        self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from archguard.analysis.community import get_seed_from_repo
        from unittest.mock import MagicMock
        import sys

        mock_pydriller = MagicMock()
        mock_pydriller.Repository.side_effect = Exception("No repo")

        with monkeypatch.context() as m:
            m.setitem(sys.modules, "pydriller", mock_pydriller)
            assert get_seed_from_repo(tmp_path) == 42
