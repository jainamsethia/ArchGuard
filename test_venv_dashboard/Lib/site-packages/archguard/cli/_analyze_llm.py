from typing import Any
from archguard.cli._analyze_output import _console
from archguard.utils.output import vprint
from archguard.analysis.layers import AnalysisResult
from archguard.cli._analyze_core import attach_explanations
from archguard.cli._analyze_options import AnalyzeOptions
from archguard.utils.tty import is_tty

def _run_llm_explanation(
    result: AnalysisResult, contract: dict[str, Any], opts: AnalyzeOptions
) -> AnalysisResult:
    quiet = opts.ctx.obj.get("quiet", False)
    use_rich = is_tty() and not quiet
    if (
        not opts.skip_explanation
        and result.archdebt.should_fail_ci
        and result.violations
    ):
        vprint(
            f"Requesting LLM explanations for {len(result.violations)} violations...",
            opts.ctx,
            level="debug",
        )
        progress = None
        if use_rich:
            from rich.progress import Progress, SpinnerColumn, TextColumn

            progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=_console,
                transient=True,
            )
            progress.start()
            progress.add_task(
                "[yellow]Generating LLM Explanations...[/yellow]", total=None
            )
        try:
            from archguard.llm.cloud import CloudLLMExplainer
            from archguard.utils.async_utils import run_async

            explainer = CloudLLMExplainer()
            raw_explanations = run_async(
                explainer.explain_violations_concurrent(
                    result.violations, contract, result.changed_files
                )
            )
            explanations = []
            for exp in raw_explanations:
                if isinstance(exp, Exception):
                    import logging
                    logging.getLogger(__name__).warning(f"Concurrent explanation failed: {exp}")
                    explanations.append("[Explanation unavailable]")
                else:
                    explanations.append(exp)
            vprint("LLM explanations received and attached.", opts.ctx, level="debug")
            result = attach_explanations(result, explanations)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                f"Failed to initialize or run LLM explainer: {exc}"
            )
        finally:
            if progress:
                progress.stop()
    return result
