"""Regex-based secret redaction pre-filter for LLM input."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RedactionResult:
    """Result of applying secret redaction to text."""

    text: str
    redactions: list[str]  # labels that were redacted, e.g. ["ANTHROPIC_KEY", "JWT"]


SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"\bsk-ant-[a-zA-Z0-9\-]{50,150}\b", "ANTHROPIC_KEY"),
    (r"\bsk-[a-zA-Z0-9]{48}\b", "OPENAI_KEY"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS_KEY"),
    (
        r"(?i)\baws_secret_access_key\s*=\s*['\"][A-Za-z0-9/+=]{40}['\"]",
        "AWS_SECRET_ACCESS_KEY",
    ),
    (
        r"(?i)(postgres|mysql|mongodb|redis|amqp)://[^:]+:[^@]+@[^\s\"']+",
        "DATABASE_URL",
    ),
    (r"\bghp_[a-zA-Z0-9]{36}\b", "GITHUB_PAT"),
    (
        r'(password|passwd|secret|api_key|token)\s*=\s*["\'][^"\']{8,}',
        "CREDENTIAL",
    ),
    (
        r"\beyJ[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}\b",
        "JWT",
    ),
    (
        r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
        r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b",
        "PRIVATE_IP",
    ),
]

_COMPILED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pattern), label) for pattern, label in SECRET_PATTERNS
]


def redact_secrets(text: str) -> RedactionResult:
    """Apply all ``SECRET_PATTERNS`` and replace matches with ``[REDACTED:{label}]``.

    Each label appears at most once in the *redactions* list even if multiple
    matches of the same type are found.
    """
    redacted = text
    labels_found: list[str] = []

    for compiled, label in _COMPILED:
        if compiled.search(redacted):
            if label not in labels_found:
                labels_found.append(label)
            redacted = compiled.sub(f"[REDACTED:{label}]", redacted)

    return RedactionResult(text=redacted, redactions=labels_found)


def is_safe_for_llm(text: str) -> tuple[bool, list[str]]:
    """Check whether *text* contains any detectable secrets.

    Returns ``(True, [])`` if no secrets detected, otherwise
    ``(False, [labels])`` listing the types found.
    """
    result = redact_secrets(text)
    if result.redactions:
        return False, result.redactions
    return True, []
