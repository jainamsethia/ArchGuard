"""Anthropic SDK cloud LLM explainer — primary + fallback model."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from typing import Any, TYPE_CHECKING

from archguard.config import EVENT_TRUNCATED_EXPLANATION
from archguard.llm.prompts import (
    SYSTEM_PROMPT,
    build_contract_summary,
    build_violation_prompt,
    parse_llm_response,
)
from archguard.utils.content_filter import redact_secrets

if TYPE_CHECKING:
    from archguard.analysis.layers import AnalysisResult, ViolationDetail
    from archguard.audit.logger import AuditLogger

logger: logging.Logger = logging.getLogger(__name__)

PRIMARY_MODEL: str = "claude-sonnet-4-20250514"
FALLBACK_MODEL: str = "claude-haiku-4-5-20251001"
MAX_TOKENS: int = 2048

_TERMINAL_PUNCT: frozenset[str] = frozenset({'.', '!', '?', '"', "'"})


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

        safe_violations = self._redact_violations(result.violations)
        summary = build_contract_summary(contract)
        prompt = build_violation_prompt(
            safe_violations, summary, result.changed_files,
        )

        # Try primary, then fallback
        response_text: str = ""
        stop_reason: str = ""
        model_used: str = ""

        for model in (PRIMARY_MODEL, FALLBACK_MODEL):
            try:
                response_text, stop_reason = self._call_api(prompt, model)
                model_used = model
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM call to %s failed: %s", model, exc)
                continue
        else:
            # Both models failed
            return LLMExplanationResult(
                unavailable=True,
                failure_reason="All LLM models failed",
            )

        # Detect truncation
        truncated = False
        if stop_reason != "end_turn":
            truncated = True
        elif response_text and response_text.rstrip()[-1:] not in _TERMINAL_PUNCT:
            truncated = True

        # Parse response
        explanations = parse_llm_response(
            response_text, len(result.violations),
        )

        if truncated:
            # Append truncation note to last explanation
            if explanations:
                explanations[-1] = (
                    explanations[-1] + " [Note: explanation was truncated]"
                )
            if self._audit:
                self._audit.log(
                    EVENT_TRUNCATED_EXPLANATION,
                    model=model_used,
                    violation_count=len(result.violations),
                )

        return LLMExplanationResult(
            explanations=explanations,
            model_used=model_used,
            truncated=truncated,
        )

    def _call_api(self, prompt: str, model: str) -> tuple[str, str]:
        """Call the Anthropic API. Lazy-imports the SDK."""
        import anthropic  # lazy import

        client: Any = anthropic.Anthropic(api_key=self._api_key)
        message: Any = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
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
