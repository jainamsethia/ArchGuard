from __future__ import annotations
from typing import Any
from archguard.analysis.layers import AnalysisResult
from archguard.cli.analyze_cmd import AnalyzeOptions
from archguard.utils.severity import Severity
from rich.console import Console

_console = Console()


def _post_github_annotations(
    result: AnalysisResult, opts: AnalyzeOptions, contract: dict[str, Any]
) -> None:
    if not (opts.repo_slug and not opts.dry_run):
        return
    import os

    commit_sha = getattr(result, "commit_sha", "")
    v_list_out = []
    for v in result.violations:
        v_list_out.append(
            {
                "type": "layer",
                "layer": getattr(v, "layer", 0),
                "file": str(getattr(v, "file_path", getattr(v, "module", ""))),
                "message": getattr(v, "message", ""),
                "severity": getattr(v, "severity", Severity.LOW).value,
                "suppressed": getattr(v, "suppressed", False),
                "explanation": getattr(v, "explanation", ""),
            }
        )
    try:
        token = os.environ.get("GITHUB_TOKEN")
        head_sha = os.environ.get("GITHUB_SHA") or commit_sha
        if token and head_sha:
            from archguard.github.checks import ChecksAPIClient
            from archguard.github.annotation_builder import violations_to_annotations

            checks_client = ChecksAPIClient(token=token, repo_full_name=opts.repo_slug)
            annotations = violations_to_annotations(v_list_out)
            fail_threshold = float(contract.get("fail_threshold", 0.75))
            conclusion = (
                "failure"
                if result.archdebt.composite_score > fail_threshold
                else "success"
            )
            from typing import Literal, cast

            conclusion_typed = cast(
                "Literal['success', 'failure', 'neutral', 'cancelled', 'skipped']",
                conclusion,
            )
            checks_client.create_check_run(
                name="ArchGuard",
                head_sha=head_sha,
                status="completed",
                conclusion=conclusion_typed,
                title=f"Health Score: {result.archdebt.health_score:.1f} (Grade {result.archdebt.health_grade})",
                summary="ArchGuard analysis complete.",
                annotations=annotations,
            )
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(f"Non-critical failure in Checks API: {e}")
    try:
        from archguard.github.client import post_comment
        from archguard.github.comments import PRCommentManager

        token = os.environ.get("GITHUB_TOKEN")
        client = None
        if token:
            from archguard.github.client import GitHubClient

            try:
                client = GitHubClient(token=token)
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(
                    f"Non-critical failure in GitHubClient init: {e}"
                )
        if client is None:
            return
        manager = PRCommentManager(client)
        body = manager.format_report(result)
        post_comment(opts.repo_slug, body, pr_number=opts.pr_number, token=token)
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            f"Non-critical failure in PR comment posting: {e}"
        )
        _console.print("[yellow]Warning: Failed to post PR comment.[/yellow]")
