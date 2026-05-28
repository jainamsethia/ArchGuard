"""PR comment create/update/delete for ArchGuard reports."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from archguard.github.client import GitHubClient

if TYPE_CHECKING:
    from archguard.analysis.layers import AnalysisResult

logger: logging.Logger = logging.getLogger(__name__)

ARCHGUARD_MARKER: str = "<!-- archguard-report -->"
RETRY_DELAY_SECONDS: float = 3.0

_BAND_EMOJI: dict[str, str] = {
    "Healthy": "✅ Healthy",
    "Watch": "⚠️ Watch",
    "Warn": "🔶 Warn",
    "Critical": "🚨 Critical",
}

_LAYER_LABELS: list[str] = [
    "L1 Import Boundaries",
    "L2 Coupling Delta",
    "L3 Semantic Drift",
    "L4 Duplication",
]


class PRCommentManager:
    """Manages ArchGuard report comments on GitHub PRs."""

    def __init__(self, client: GitHubClient) -> None:
        self._client: GitHubClient = client

    def find_existing_comment(
        self,
        repo_slug: str,
        pr_number: int,
    ) -> int | None:
        """Search PR comments for ``ARCHGUARD_MARKER``. Returns comment_id or None."""
        try:
            pr = self._client.get_pr(repo_slug, pr_number)
            for comment in pr.get_issue_comments():
                if ARCHGUARD_MARKER in (comment.body or ""):
                    return int(comment.id)
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(f"Non-critical failure in find_existing_comment: {e}")
        return None

    def post_or_update(
        self,
        repo_slug: str,
        pr_number: int,
        body: str,
    ) -> int:
        """Post or update the ArchGuard report comment.

        Strategy:
          1. Find existing comment → PATCH (edit).
          2. On PATCH failure → wait, retry once.
          3. On second failure → POST new comment.
          4. No existing → POST new comment.
        """
        full_body = f"{ARCHGUARD_MARKER}\n{body}" if ARCHGUARD_MARKER not in body else body

        existing_id = self.find_existing_comment(repo_slug, pr_number)

        if existing_id is not None:
            try:
                repo = self._client.get_repo(repo_slug)
                comment = repo.get_comment(existing_id)
                comment.edit(full_body)
                return existing_id
            except Exception as e:  # noqa: BLE001
                import logging
                logging.getLogger(__name__).warning(f"Non-critical failure in post_or_update PATCH: {e}")
                logger.warning("PATCH failed, retrying after %ss", RETRY_DELAY_SECONDS)
                time.sleep(RETRY_DELAY_SECONDS)
                try:
                    repo = self._client.get_repo(repo_slug)
                    comment = repo.get_comment(existing_id)
                    comment.edit(full_body)
                    return existing_id
                except Exception as e:  # noqa: BLE001
                    import logging
                    logging.getLogger(__name__).warning(f"Non-critical failure in post_or_update PATCH retry: {e}")
                    logger.warning("PATCH retry failed, posting new comment")

        # POST new comment
        pr = self._client.get_pr(repo_slug, pr_number)
        new_comment: Any = pr.create_issue_comment(full_body)
        return int(new_comment.id)

    def delete_stale(self, repo_slug: str, comment_id: int) -> None:
        """Fire-and-forget delete. Swallow all exceptions."""
        try:
            repo = self._client.get_repo(repo_slug)
            comment = repo.get_comment(comment_id)
            comment.delete()
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(f"Non-critical failure in delete_stale: {e}")

    def format_report(self, result: AnalysisResult) -> str:
        """Build PR comment markdown from ``AnalysisResult``."""
        archdebt = result.archdebt
        band_str = _BAND_EMOJI.get(archdebt.band.value, archdebt.band.value)
        ci_str = "CI PASSED" if not archdebt.should_fail_ci else "CI FAILED"

        lines: list[str] = [
            ARCHGUARD_MARKER,
            "## ArchGuard Report",
            "",
            f"**ArchDebt Score**: {archdebt.composite_score:.2f} — {band_str}",
            "",
            ci_str,
            "",
        ]

        # Violations table
        if result.violations:
            lines.append(f"### Violations ({len(result.violations)})")
            lines.append("| Layer | Module | Issue | Commit |")
            lines.append("|-------|--------|-------|--------|")
            for v in result.violations:
                lines.append(
                    f"| L{v.layer} | {v.module} | {v.message} | "
                    f"`{v.commit_sha[:7]}` |"
                )
                if v.explanation:
                    lines.append(f"")
                    lines.append(f"*{v.explanation}*")
                    lines.append(f"")
            lines.append("")

        # Layer scores in collapsible section
        layer_values = [
            archdebt.layer_scores.layer1_violation,
            archdebt.layer_scores.layer2_coupling,
            archdebt.layer_scores.layer3_drift,
            archdebt.layer_scores.layer4_duplication,
        ]

        lines.append("<details>")
        lines.append("<summary>Layer Scores</summary>")
        lines.append("")
        lines.append("| Layer | Score | Weight |")
        lines.append("|-------|-------|--------|")
        for i, label in enumerate(_LAYER_LABELS):
            lines.append(
                f"| {label} | {layer_values[i]:.2f} | "
                f"{archdebt.weights[i]:.2f} |"
            )
        lines.append("")
        lines.append("</details>")

        return "\n".join(lines)
