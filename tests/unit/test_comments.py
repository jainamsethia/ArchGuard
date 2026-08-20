"""Unit tests for archguard.github.comments and archguard.github.commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from archguard.analysis.layers import AnalysisResult, ViolationDetail
from archguard.analysis.scoring import (
    ArchDebtBand,
    ArchDebtResult,
    LayerScores,
)
from archguard.github.commands import (
    ArchGuardCommand,
    parse_commands,
)
from archguard.github.comments import (
    ARCHGUARD_MARKER,
    PRCommentManager,
)


def _make_result(
    composite: float = 0.30,
    band: ArchDebtBand = ArchDebtBand.HEALTHY,
    should_fail: bool = False,
    violations: list[ViolationDetail] | None = None,
) -> AnalysisResult:
    """Build a minimal AnalysisResult for testing."""
    scores = LayerScores(0.10, 0.10, 0.05, 0.05)
    archdebt = ArchDebtResult(
        composite_score=composite,
        band=band,
        layer_scores=scores,
        weights=(0.25, 0.25, 0.25, 0.25),
        per_component_breach=False,
        composite_breach=should_fail,
        should_fail_ci=should_fail,
        fail_reasons=[],
    )
    return AnalysisResult(
        archdebt=archdebt,
        violations=violations or [],
        layer_scores=scores,
        modules_analyzed=2,
        changed_files=["a.py", "b.py"],
        commit_sha="a1b2c3d",
    )


class TestPRCommentManager:
    """Tests for PRCommentManager."""

    def test_no_existing_comment_posts(self) -> None:
        """No existing comment -> POST called once."""
        mock_client = MagicMock()
        mock_client.get_issue_comments.return_value = []

        mgr = PRCommentManager(mock_client)
        comment_id = mgr.post_or_update("org/repo", 1, "body")

        assert comment_id == 0
        mock_client.post_comment.assert_called_once()

    def test_existing_comment_patches(self) -> None:
        """Existing comment found -> PATCH (edit) called."""
        mock_client = MagicMock()
        mock_client.get_issue_comments.return_value = [
            {"id": 99, "body": ARCHGUARD_MARKER + "\nold body"}
        ]

        mgr = PRCommentManager(mock_client)
        comment_id = mgr.post_or_update("org/repo", 1, "new body")

        assert comment_id == 99
        mock_client.update_comment.assert_called_once()

    def test_patch_fails_retries(self) -> None:
        """PATCH fails -> retry after delay."""
        mock_client = MagicMock()
        mock_client.get_issue_comments.return_value = [
            {"id": 99, "body": ARCHGUARD_MARKER + "\nold"}
        ]
        mock_client.update_comment.side_effect = [Exception("API error"), None]

        mgr = PRCommentManager(mock_client)
        with patch("archguard.github.comments.time.sleep"):
            comment_id = mgr.post_or_update("org/repo", 1, "body")

        assert comment_id == 99

    def test_patch_fails_twice_posts_new(self) -> None:
        """PATCH fails twice -> POST new comment."""
        mock_client = MagicMock()
        mock_client.get_issue_comments.return_value = [
            {"id": 99, "body": ARCHGUARD_MARKER + "\nold"}
        ]
        mock_client.update_comment.side_effect = Exception("API error")

        mgr = PRCommentManager(mock_client)
        with patch("archguard.github.comments.time.sleep"):
            comment_id = mgr.post_or_update("org/repo", 1, "body")

        assert comment_id == 0

    def test_format_report_healthy_ci_passed(self) -> None:
        """HEALTHY result -> contains 'CI PASSED'."""
        mock_client = MagicMock()
        mgr = PRCommentManager(mock_client)
        result = _make_result(band=ArchDebtBand.HEALTHY, should_fail=False)
        body = mgr.format_report(result)
        assert "CI PASSED" in body

    def test_format_report_critical_ci_failed(self) -> None:
        """CRITICAL result -> contains 'CI FAILED'."""
        mock_client = MagicMock()
        mgr = PRCommentManager(mock_client)
        result = _make_result(
            composite=0.80,
            band=ArchDebtBand.CRITICAL,
            should_fail=True,
        )
        body = mgr.format_report(result)
        assert "CI FAILED" in body

    def test_format_report_starts_with_marker(self) -> None:
        """Report starts with ARCHGUARD_MARKER."""
        mock_client = MagicMock()
        mgr = PRCommentManager(mock_client)
        result = _make_result()
        body = mgr.format_report(result)
        assert body.startswith(ARCHGUARD_MARKER)

    def test_format_report_violations_have_commit(self) -> None:
        """Violation rows include commit SHA."""
        mock_client = MagicMock()
        mgr = PRCommentManager(mock_client)
        violations = [
            ViolationDetail(
                layer=1,
                module="payments",
                message="Imports `auth.internal` (disallowed)",
                commit_sha="a1b2c3d",
                file_path="payments/views.py",
            ),
        ]
        result = _make_result(violations=violations)
        body = mgr.format_report(result)
        assert "`a1b2c3d`" in body

    def test_delete_stale_swallows_exception(self) -> None:
        """delete_stale raises -> swallowed silently."""
        mock_client = MagicMock()
        mock_client.delete_comment.side_effect = Exception("API down")

        mgr = PRCommentManager(mock_client)
        # Should not raise
        mgr.delete_stale("org/repo", 42)


class TestParseCommands:
    """Tests for parse_commands."""

    def test_accept_contract(self) -> None:
        """/archguard accept-contract payments -> parsed correctly."""
        result = parse_commands(
            "/archguard accept-contract payments",
            1,
            "user1",
        )
        assert len(result) == 1
        assert result[0].command == ArchGuardCommand.ACCEPT_CONTRACT
        assert result[0].args[0] == "payments"

    def test_unknown_command_ignored(self) -> None:
        """Unknown command -> empty list."""
        result = parse_commands(
            "/archguard do-something",
            1,
            "user1",
        )
        assert len(result) == 0

    def test_multiple_commands(self) -> None:
        """Multiple commands in one body -> all returned."""
        body = (
            "/archguard accept-contract payments\n"
            "some text\n"
            "/archguard reject-contract orders\n"
        )
        result = parse_commands(body, 1, "user1")
        assert len(result) == 2
        assert result[0].command == ArchGuardCommand.ACCEPT_CONTRACT
        assert result[1].command == ArchGuardCommand.REJECT_CONTRACT
