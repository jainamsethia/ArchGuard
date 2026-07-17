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
                # When testing fallback, raise on PRIMARY_MODEL if instructed via env var
                if os.getenv("ARCHGUARD_MOCK_PRIMARY_FAIL") == "1":
                    # the loop will catch this and try fallback
                    pass
                else:
                    return "Mock LLM explanation for testing"
            
            async with semaphore:
                async with anthropic.AsyncAnthropic(api_key=self._api_key) as client:
                    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
                        try:
                            if os.getenv("ARCHGUARD_MOCK_LLM") == "1":
                                if model == PRIMARY_MODEL and os.getenv("ARCHGUARD_MOCK_PRIMARY_FAIL") == "1":
                                    raise RuntimeError("Simulated primary failure")
                                return f"Mock LLM explanation for testing (model={model})"
                                
                            response = await client.messages.create(
                                model=model,
                                max_tokens=500,
                                messages=[{"role": "user", "content": prompt}],
                            )
                            return str(response.content[0].text)  # type: ignore[union-attr]
                        except Exception:
                            if model == FALLBACK_MODEL:
                                raise
                            continue
            return "Explanation unavailable"

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
