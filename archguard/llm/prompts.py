"""Violation explanation prompt templates for LLM integration."""

from __future__ import annotations

import re
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from archguard.analysis.layers import ViolationDetail

SYSTEM_PROMPT: str = """\
You are ArchGuard, an architectural drift analysis assistant for Python codebases.
Your role is to explain architectural violations clearly and concisely to developers.
Focus on:
- What boundary was crossed and why it matters
- The specific module or import involved
- A concrete, actionable fix (1–3 sentences max)
Do not moralize. Do not repeat the violation data verbatim. Be direct and practical.
Output plain text only. No markdown headers. No bullet points unless listing 3+ items.
Maximum 150 words per violation explanation.
"""

_NUMBERED_RE: re.Pattern[str] = re.compile(r"^\d+\.\s+", re.MULTILINE)


def build_contract_summary(contract: dict[str, Any]) -> str:
    """Build a summary string from contract data.

    Returns: ``"5 modules defined: payments, orders, auth, core, utils"``
    """
    modules: list[dict[str, Any]] = contract.get("modules", [])
    names = [m.get("name", "unknown") for m in modules]
    return f"{len(names)} modules defined: {', '.join(names)}"


def build_violation_prompt(
    violations: list[ViolationDetail],
    contract_summary: str,
    changed_files: list[str],
) -> str:
    """Build user message for the LLM.

    Truncates *changed_files* to max 20 items and *violations* to max 10.
    """
    # Truncate changed files
    max_files = 20
    if len(changed_files) > max_files:
        files_str = ", ".join(changed_files[:max_files])
        files_str += f" ... and {len(changed_files) - max_files} more"
    else:
        files_str = ", ".join(changed_files) if changed_files else "(none)"

    lines: list[str] = [
        f"Repository has {len(violations)} architectural violation(s) in this batch.",
        "",
        "Contract summary:",
        contract_summary,
        "",
        f"Changed files: {files_str}",
        "",
        "Violations:",
    ]

    for i, v in enumerate(violations, start=1):
        sev_val = str(getattr(v, "severity", "low")).upper()
        lines.append(
            f"{i}. [L{v.layer}] {v.module}: {v.message} "
            f"(severity: {sev_val}, commit: {v.commit_sha})"
        )

    lines.append("")
    lines.append(
        "For each violation, provide a concise explanation and actionable fix."
    )
    lines.append("Number your responses to match the violation numbers above.")

    return "\n".join(lines)


def parse_llm_response(response_text: str, violation_count: int) -> list[str]:
    """Split numbered LLM response into per-violation explanations.

    If parsing fails (response not numbered): return ``[response_text] * violation_count``.
    Pads with ``"Explanation unavailable."`` if fewer explanations than violations.
    Truncates if more explanations than violations.
    """
    # Find all numbered items
    splits = _NUMBERED_RE.split(response_text)
    # First element is text before first number (often empty)
    items = [s.strip() for s in splits[1:] if s.strip()]

    if not items:
        # Response isn't numbered — duplicate entire text for each violation
        return [response_text.strip()] * violation_count

    # Pad if fewer
    while len(items) < violation_count:
        items.append("Explanation unavailable.")

    # Truncate if more
    return items[:violation_count]
