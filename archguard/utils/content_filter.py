import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RedactionResult:
    """Result of applying secret redaction to text."""
    text: str
    redactions: list[str]


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
    # Fine-grained GitHub PATs (github_pat_...) — fix for MED-03
    (r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "GITHUB_PAT_FINE"),
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

# Environment variables whose live values must be redacted on exact match.
# These are operator secrets whose shape cannot be inferred from a regex.
_ENV_SECRET_VARS: tuple[str, ...] = (
    "ARCHGUARD_DASHBOARD_TOKEN",
    "GITHUB_TOKEN",
    "ARCHGUARD_LLM_API_KEY",
    "OPENAI_API_KEY",
)


def _get_env_secrets() -> list[tuple[str, str]]:
    """Return (value, label) pairs for live operator secrets that are set."""
    secrets = []
    for var in _ENV_SECRET_VARS:
        val = os.environ.get(var, "").strip()
        if len(val) >= 8:  # skip trivially short values (empty, placeholder, etc.)
            secrets.append((val, var))
    return secrets


def redact_secrets(text: str) -> RedactionResult:
    """Apply all SECRET_PATTERNS and live environment secrets to redact sensitive data."""
    redacted = text
    labels_found: list[str] = []

    # Pass 1: exact-match redaction for operator secrets from the environment
    for secret_val, label in _get_env_secrets():
        if secret_val in redacted:
            if label not in labels_found:
                labels_found.append(label)
            redacted = redacted.replace(secret_val, f"[REDACTED:{label}]")

    # Pass 2: shape-based regex patterns
    for compiled, label in _COMPILED:
        if compiled.search(redacted):
            if label not in labels_found:
                labels_found.append(label)
            redacted = compiled.sub(f"[REDACTED:{label}]", redacted)

    return RedactionResult(text=redacted, redactions=labels_found)


def is_safe_for_llm(text: str) -> tuple[bool, list[str]]:
    """Check whether *text* contains any detectable secrets."""
    result = redact_secrets(text)
    if result.redactions:
        return False, result.redactions
    return True, []
