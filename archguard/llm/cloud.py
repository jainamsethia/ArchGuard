"""Gemini cloud LLM explainer - primary + fallback model.

Layer 4 attaches a natural-language explanation to each violation. Two model
tiers are used, not one: the primary is tried first, and a cheaper, faster tier
takes over when the primary is rate-limited or unreachable. That resilience
pattern predates the move to Gemini (it was Sonnet -> Haiku) and is kept
deliberately -- an explanation that arrives from the cheaper model is worth far
more than a run that fails because the better model was busy.

What did change: the fallback now triggers only on genuinely transient
conditions. It previously caught bare ``Exception``, so a bad API key burned an
attempt on both tiers and reported the second failure, hiding the real cause.
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from archguard.config import EVENT_TRUNCATED_EXPLANATION
from archguard.llm.gemini import (
    NON_RETRYABLE_ERRORS,
    RETRYABLE_ERRORS,
    GeminiAuthError,
    GeminiClient,
    GeminiRateLimitError,
    fallback_model,
    primary_model,
    resolve_api_key,
)
from archguard.llm.prompts import (
    SYSTEM_PROMPT,
    build_contract_summary,
    build_violation_prompt,
)
from archguard.utils.content_filter import redact_secrets
from archguard.utils.retry import with_retry

if TYPE_CHECKING:
    from archguard.analysis.layers import ViolationDetail
    from archguard.audit.logger import AuditLogger

logger: logging.Logger = logging.getLogger(__name__)

PRIMARY_MODEL: str = primary_model()
FALLBACK_MODEL: str = fallback_model()
# Shares the reasoning-token problem described in gemini.DEFAULT_MAX_TOKENS.
# This budget covers both L4 explanations and contract inference, and the latter
# parses its response as JSON -- so a truncated reply there fails exactly the way
# remediation did, rather than merely reading as a cut-off sentence.
MAX_TOKENS: int = int(os.getenv("ARCHGUARD_EXPLANATION_MAX_TOKENS", "8192"))

_TERMINAL_PUNCT: frozenset[str] = frozenset({".", "!", "?", '"', "'"})

# Kept as module-level names because the retry decorator below binds them at
# import time, and tests reference them.
_CLOUD_RETRYABLE = RETRYABLE_ERRORS
_CLOUD_NON_RETRYABLE = NON_RETRYABLE_ERRORS


class CloudLLMExplainer:
    """Calls Gemini for violation explanations, with a cheaper fallback tier."""

    def __init__(
        self,
        api_key: str | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._api_key: str = resolve_api_key(api_key)
        self._audit: AuditLogger | None = audit_logger

    def _client(self) -> GeminiClient:
        return GeminiClient(api_key=self._api_key)

    async def explain_violations_concurrent(
        self,
        violations: list[ViolationDetail],
        contract: dict[str, Any],
        changed_files: list[str],
        max_concurrent: int = 5,
    ) -> list[Any]:
        """Fetch explanations for all violations concurrently."""
        import asyncio

        if not self._api_key and os.getenv("ARCHGUARD_MOCK_LLM") != "1":
            # A missing key is a configuration problem, not a per-violation
            # failure: say so once per violation rather than crashing the run.
            return ["Explanation unavailable (GEMINI_API_KEY not set)"] * len(violations)

        summary = build_contract_summary(contract)
        safe_violations = self._redact_violations(violations)
        semaphore = asyncio.Semaphore(max_concurrent)
        client = self._client()

        async def explain_one(violation: ViolationDetail) -> str:
            prompt = build_violation_prompt([violation], summary, changed_files)
            mock = os.getenv("ARCHGUARD_MOCK_LLM") == "1"
            if mock and os.getenv("ARCHGUARD_MOCK_PRIMARY_FAIL") != "1":
                print(f"--- MOCK LLM PROMPT ---\n{prompt}\n--- END MOCK LLM PROMPT ---")
                return "Mock LLM explanation for testing"

            async with semaphore:
                last_error: Exception | None = None
                for model in (PRIMARY_MODEL, FALLBACK_MODEL):
                    try:
                        if mock:
                            if (
                                model == PRIMARY_MODEL
                                and os.getenv("ARCHGUARD_MOCK_PRIMARY_FAIL") == "1"
                            ):
                                # Raised as a retryable error so the mock
                                # exercises the real fallback branch.
                                raise GeminiRateLimitError("Simulated primary failure")
                            return f"Mock LLM explanation for testing (model={model})"

                        text, finish = await client.acomplete(
                            prompt,
                            system=SYSTEM_PROMPT,
                            model=model,
                            max_tokens=MAX_TOKENS,
                        )
                        return self._flag_truncation(text, finish)
                    except NON_RETRYABLE_ERRORS:
                        # Credentials or a malformed request will fail identically
                        # on the cheaper tier; retrying only hides the real cause.
                        raise
                    except RETRYABLE_ERRORS as exc:
                        last_error = exc
                        if model == FALLBACK_MODEL:
                            raise
                        logger.warning(
                            "Gemini %s unavailable (%s); falling back to %s",
                            model, exc, FALLBACK_MODEL,
                        )
                        continue
                if last_error is not None:
                    raise last_error
            return "Explanation unavailable"

        tasks = [explain_one(v) for v in safe_violations]
        return await asyncio.gather(*tasks, return_exceptions=True)

    @with_retry(
        max_attempts=3,
        retryable_exceptions=_CLOUD_RETRYABLE,
        non_retryable_exceptions=_CLOUD_NON_RETRYABLE,
    )
    def _call_api(self, prompt: str, model: str, system: str = SYSTEM_PROMPT) -> tuple[str, str]:
        """Call Gemini synchronously. Returns ``(text, finish_reason)``."""
        if os.getenv("ARCHGUARD_MOCK_LLM") == "1":
            return "Mock LLM explanation for testing", "stop"
        return self._client().complete(
            prompt, system=system, model=model, max_tokens=MAX_TOKENS
        )

    def _flag_truncation(self, text: str, stop_reason: str) -> str:
        """Append a truncation note (and audit-log it) when the response is cut off.

        Treated as truncated when the API reports a length cap, or when it
        stopped normally but does not end in terminal punctuation. Gemini's
        OpenAI-compatible endpoint reports this as ``length`` where Anthropic
        used ``max_tokens``; both are accepted so the check keeps working.
        """
        stripped = text.rstrip()
        truncated = stop_reason in ("length", "max_tokens") or (
            stripped != "" and stripped[-1] not in _TERMINAL_PUNCT
        )
        if not truncated:
            return text
        logger.warning("LLM explanation truncated (stop_reason=%s)", stop_reason)
        if self._audit is not None:
            self._audit.log(EVENT_TRUNCATED_EXPLANATION, stop_reason=stop_reason)
        return f"{text}\n\n[Note: explanation was truncated]"

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


__all__ = [
    "FALLBACK_MODEL",
    "PRIMARY_MODEL",
    "CloudLLMExplainer",
    "GeminiAuthError",
]
