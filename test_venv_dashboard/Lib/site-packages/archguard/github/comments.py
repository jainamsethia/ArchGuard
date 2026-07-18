"""PR comment create/update/delete for ArchGuard reports."""

from __future__ import annotations
from typing import Any

import logging
import time
from typing import TYPE_CHECKING

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


def build_fitness_section(fitness_results: list[dict[str, Any]]) -> str:
    """Build the Architecture Fitness Functions markdown section."""
    if not fitness_results:
        return ""

    total = len(fitness_results)
    passed_results = [r for r in fitness_results if r.get("passed", True)]
    failed_results = [r for r in fitness_results if not r.get("passed", True)]
    passed_count = len(passed_results)
    failed_count = len(failed_results)

    lines = ["## Architecture Fitness Functions", ""]

    if failed_count == 0:
        lines.append(f"✅ All {total} fitness function(s) passed.")
    else:
        lines.append(f"❌ {failed_count} of {total} fitness function(s) failed.")

    critical_failures = [r for r in failed_results if r.get("severity") == "critical"]
    if critical_failures:
        lines.append("")
        lines.append("### ⛔ Critical Failures")
        lines.append("")
        lines.append("| Function | Rule | Evidence |")
        lines.append("|----------|------|----------|")
        for f in critical_failures:
            lines.append(
                f"| {f.get('name', f.get('rule', ''))} | {f.get('rule', '')} | {f.get('evidence', '')} |"
            )

    warn_failures = [r for r in failed_results if r.get("severity") == "warn"]
    if warn_failures:
        lines.append("")
        lines.append("### ⚠️ Warnings")
        lines.append("")
        lines.append("| Function | Rule | Evidence |")
        lines.append("|----------|------|----------|")
        for f in warn_failures:
            lines.append(
                f"| {f.get('name', f.get('rule', ''))} | {f.get('rule', '')} | {f.get('evidence', '')} |"
            )

    if passed_results:
        lines.append("")
        first_5 = passed_results[:5]
        names = [f.get("name", f.get("rule", "")) for f in first_5]
        passing_str = f"✅ Passing: {', '.join(names)}"
        if passed_count > 5:
            passing_str += f" (+{passed_count - 5} more)"
        lines.append(passing_str)

    return "\n".join(lines)


def build_risk_section(report: Any) -> str:
    """Build the PR Risk Assessment markdown section."""
    if not report or getattr(report, "overall_risk", "none").lower() == "none":
        return ""

    overall_risk = getattr(report, "overall_risk", "unknown").lower()
    risk_emoji = {
        "critical": "🚨",
        "high": "🛑",
        "medium": "⚠️",
        "low": "ℹ️",
    }.get(overall_risk, "❓")

    lines = [
        "## Risk Assessment",
        "",
        f"**Overall Risk**: {risk_emoji} {overall_risk.capitalize()}",
        "",
    ]

    module_risks = getattr(report, "module_risks", [])
    if module_risks:
        lines.append("### Module Risk Table")
        lines.append("")
        lines.append("| Module | Risk Level | Details |")
        lines.append("|--------|------------|---------|")

        # Cap displayed modules at 10
        displayed = module_risks[:10]
        for mr in displayed:
            module_name = getattr(mr, "module", "Unknown")
            risk_level = getattr(mr, "risk_level", "Unknown").capitalize()
            reasons = getattr(mr, "reasons", [])
            details = ", ".join(reasons) if reasons else "No specific reasons provided."
            lines.append(f"| {module_name} | {risk_level} | {details} |")

        if len(module_risks) > 10:
            lines.append("")
            lines.append(f"*(+{len(module_risks) - 10} more modules hidden)*")
        lines.append("")

    return "\n".join(lines)


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
            comments = self._client.get_issue_comments(repo_slug, pr_number)
            for comment in comments:
                if ARCHGUARD_MARKER in (comment.get("body") or ""):
                    return int(comment["id"])
        except Exception as e:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).warning(
                f"Non-critical failure in find_existing_comment: {e}"
            )
        return None

    def post_or_update(
        self,
        repo_slug: str,
        pr_number: int,
        body: str,
    ) -> int:
        """Post or update the ArchGuard report comment.

        Strategy:
          1. Find existing comment -> PATCH (edit).
          2. On PATCH failure -> wait, retry once.
          3. On second failure -> POST new comment.
          4. No existing -> POST new comment.
        """
        full_body = (
            f"{ARCHGUARD_MARKER}\n{body}" if ARCHGUARD_MARKER not in body else body
        )

        existing_id = self.find_existing_comment(repo_slug, pr_number)

        if existing_id is not None:
            try:
                self._client.update_comment(repo_slug, existing_id, full_body)
                return existing_id
            except Exception as e:  # noqa: BLE001
                import logging

                logging.getLogger(__name__).warning(
                    f"Non-critical failure in post_or_update PATCH: {e}"
                )
                logger.warning("PATCH failed, retrying after %ss", RETRY_DELAY_SECONDS)
                time.sleep(RETRY_DELAY_SECONDS)
                try:
                    self._client.update_comment(repo_slug, existing_id, full_body)
                    return existing_id
                except Exception as e:  # noqa: BLE001
                    import logging

                    logging.getLogger(__name__).warning(
                        f"Non-critical failure in post_or_update PATCH retry: {e}"
                    )
                    logger.warning("PATCH retry failed, posting new comment")

        # POST new comment
        self._client.post_comment(repo_slug, full_body, pr_number)
        # Just return 0 since post_comment doesn't return the ID right now, but we don't strictly need it for tests
        return 0

    def delete_stale(self, repo_slug: str, comment_id: int) -> None:
        """Fire-and-forget delete. Swallow all exceptions."""
        try:
            self._client.delete_comment(repo_slug, comment_id)
        except Exception as e:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).warning(
                f"Non-critical failure in delete_stale: {e}"
            )

    def format_report(self, result: AnalysisResult) -> str:
        """Build PR comment markdown from ``AnalysisResult``."""
        archdebt = result.archdebt
        band_str = _BAND_EMOJI.get(archdebt.band.value, archdebt.band.value)
        ci_str = "CI PASSED" if not archdebt.should_fail_ci else "CI FAILED"

        lines: list[str] = [
            ARCHGUARD_MARKER,
            "## ArchGuard Report",
            "",
            f"**ArchDebt Score**: {archdebt.composite_score:.2f} - {band_str}",
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
                    f"| L{v.layer} | {v.module} | {v.message} | `{v.commit_sha[:7]}` |"
                )
                if v.explanation:
                    lines.append("")
                    lines.append(f"*{v.explanation}*")
                    lines.append("")
            lines.append("")

        fitness_data = getattr(result, "metrics", {}).get("fitness_results", [])
        if fitness_data:
            fitness_section = build_fitness_section(fitness_data)
            if fitness_section:
                lines.append(fitness_section)
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
                f"| {label} | {layer_values[i]:.2f} | {archdebt.weights[i]:.2f} |"
            )
        lines.append("")
        lines.append("</details>")

        return "\n".join(lines)
