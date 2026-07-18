"""Anthropic SDK cloud LLM explainer - primary + fallback model."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from typing import Any, TYPE_CHECKING

try:
    import anthropic

    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False
    anthropic = None  # type: ignore[assignment]

from archguard.config import EVENT_TRUNCATED_EXPLANATION
from archguard.llm.prompts import (
    SYSTEM_PROMPT,
    build_contract_summary,
    build_violation_prompt,
    parse_llm_response,
)
from archguard.utils.content_filter import redact_secrets
from archguard.utils.retry import with_retry

if TYPE_CHECKING:
    from archguard.analysis.layers import AnalysisResult, ViolationDetail
    from archguard.audit.logger import AuditLogger

logger: logging.Logger = logging.getLogger(__name__)

PRIMARY_MODEL: str = os.getenv("ARCHGUARD_PRIMARY_MODEL", "claude-sonnet-4-20250514")
FALLBACK_MODEL: str = os.getenv("ARCHGUARD_FALLBACK_MODEL", "claude-haiku-4-5-20251001")
MAX_TOKENS: int = 2048

_TERMINAL_PUNCT: frozenset[str] = frozenset({".", "!", "?", '"', "'"})

_CLOUD_RETRYABLE = (
    (
        anthropic.APIConnectionError,
        anthropic.RateLimitError,
        anthropic.InternalServerError,
    )
    if _ANTHROPIC_AVAILABLE
    else (Exception,)
)
_CLOUD_NON_RETRYABLE = (
    (
        anthropic.AuthenticationError,
        anthropic.PermissionDeniedError,
        ValueError,
        TypeError,
    )
    if _ANTHROPIC_AVAILABLE
    else (ValueError, TypeError)
)


@dataclass
class LLMExplanationResult:
    """Result of an LLM explanation request."""

    explanations: list[str] = field(default_factory=list)
    model_used: str = ""
    truncated: bool = False
    unavailable: bool = False
    failure_reason: str = ""


class CloudLLMExplainer:
    """Calls Anthropic Claude for violation explanations with fallback."""

    def __init__(
        self,
        api_key: str | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._api_key: str = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._audit: AuditLogger | None = audit_logger

    def explain(
        self,
        result: AnalysisResult,
        contract: dict[str, Any],
    ) -> LLMExplanationResult:
        """Generate explanations for violations using Claude.

        1. Redact secrets from all violation messages before sending.
        2. Try PRIMARY_MODEL first.
        3. On any exception: try FALLBACK_MODEL.
        4. On both failing: return unavailable=True.
        5. Detect truncation via stop_reason or terminal punctuation.
        6. Parse response via parse_llm_response().
        """
        if not result.violations:
            return LLMExplanationResult()

        if not _ANTHROPIC_AVAILABLE:
            return LLMExplanationResult(
                unavailable=True,
                failure_reason='Intelligence layer skipped: install with pip install -e ".[cloud]"'
            )

        safe_violations = self._redact_violations(result.violations)
        summary = build_contract_summary(contract)

        all_explanations: list[str] = []
        model_used: str = ""
        truncated = False
        unavailable = False
        failure_reason = ""

        chunk_size = 20
        for i in range(0, len(safe_violations), chunk_size):
            chunk = safe_violations[i : i + chunk_size]
            prompt = build_violation_prompt(
                chunk,
                summary,
                result.changed_files,
            )

            response_text: str = ""
            stop_reason: str = ""
            chunk_success = False

            for model in (PRIMARY_MODEL, FALLBACK_MODEL):
                try:
                    response_text, stop_reason = self._call_api(prompt, model)
                    model_used = model
                    chunk_success = True
                    break
                except Exception as err:
                    logger.warning("LLMError: %s", getattr(err, 'message', str(err)))
                    continue

            if not chunk_success:
                # Fallback to per-violation calls for this chunk
                for single_violation in chunk:
                    single_prompt = build_violation_prompt(
                        [single_violation], summary, result.changed_files
                    )
                    single_success = False
                    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
                        try:
                            s_resp, s_stop = self._call_api(single_prompt, model)
                            model_used = model
                            single_success = True
                            single_exps = parse_llm_response(s_resp, 1)
                            all_explanations.extend(single_exps)
                            break
                        except Exception:
                            continue
                    if not single_success:
                        all_explanations.append("Explanation unavailable.")
                        unavailable = True
                        failure_reason = "All LLM models failed on fallback"
                continue

            # Detect truncation
            if stop_reason != "end_turn":
                truncated = True
            elif response_text and response_text.rstrip()[-1:] not in _TERMINAL_PUNCT:
                truncated = True

            # Parse chunk response
            chunk_explanations = parse_llm_response(
                response_text,
                len(chunk),
            )
            all_explanations.extend(chunk_explanations)

        if unavailable and all(
            e == "Explanation unavailable." for e in all_explanations
        ):
            return LLMExplanationResult(
                unavailable=True,
                failure_reason=failure_reason,
            )

        if truncated:
            # Append truncation note to last explanation
            if all_explanations:
                all_explanations[-1] = (
                    all_explanations[-1] + " [Note: explanation was truncated]"
                )
            if self._audit:
                self._audit.log(
                    EVENT_TRUNCATED_EXPLANATION,
                    model=model_used,
                    violation_count=len(result.violations),
                )

        return LLMExplanationResult(
            explanations=all_explanations,
            model_used=model_used,
            truncated=truncated,
            unavailable=unavailable,
        )

    async def explain_violations_concurrent(
        self,
        violations: list[ViolationDetail],
        contract: dict[str, Any],
        changed_files: list[str],
        max_concurrent: int = 5,
    ) -> list[Any]:
        """Fetch explanations for all violations concurrently."""
        import asyncio

        if not _ANTHROPIC_AVAILABLE:
            raise RuntimeError(
                'The Anthropic SDK is not installed. Run: pip install "archguard[cloud]"'
            )

        if not self._api_key and os.getenv("ARCHGUARD_MOCK_LLM") != "1":
            # Just return a clear explanation unavailable for each violation instead of crashing
            return ["Explanation unavailable (ANTHROPIC_API_KEY not set)"] * len(violations)

        summary = build_contract_summary(contract)
        safe_violations = self._redact_violations(violations)
        semaphore = asyncio.Semaphore(max_concurrent)

        async def explain_one(violation: ViolationDetail) -> str:
            prompt = build_violation_prompt([violation], summary, changed_files)
            if os.getenv("ARCHGUARD_MOCK_LLM") == "1":
                print(f"--- MOCK LLM PROMPT ---\n{prompt}\n--- END MOCK LLM PROMPT ---")
                return "Mock LLM explanation for testing"
            async with semaphore:
                # Use anthropic's async client
                async with anthropic.AsyncAnthropic(api_key=self._api_key) as client:
                    response = await client.messages.create(
                        model=FALLBACK_MODEL,  # Use fallback model for explanations
                        max_tokens=500,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    return str(response.content[0].text)  # type: ignore[union-attr]

        tasks = [explain_one(v) for v in safe_violations]
        return await asyncio.gather(*tasks, return_exceptions=True)

    @with_retry(
        max_attempts=3,
        retryable_exceptions=_CLOUD_RETRYABLE,
        non_retryable_exceptions=_CLOUD_NON_RETRYABLE,
    )
    def _call_api(self, prompt: str, model: str, system: str = SYSTEM_PROMPT) -> tuple[str, str]:
        """Call the Anthropic API. Lazy-imports the SDK."""
        if os.getenv("ARCHGUARD_MOCK_LLM") == "1":
            return "Mock LLM explanation for testing", "end_turn"
        if not _ANTHROPIC_AVAILABLE:
            raise RuntimeError(
                'The Anthropic SDK is not installed. Run: pip install "archguard[cloud]"'
            )

        client: Any = anthropic.Anthropic(api_key=self._api_key)
        message: Any = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return str(message.content[0].text), str(message.stop_reason)

    def _redact_violations(
        self,
        violations: list[ViolationDetail],
    ) -> list[ViolationDetail]:
        """Return new list with secrets redacted from each violation.message."""
        result: list[ViolationDetail] = []
        for v in violations:
            redacted = redact_secrets(v.message)
            result.append(replace(v, message=redacted.text))
        return result
