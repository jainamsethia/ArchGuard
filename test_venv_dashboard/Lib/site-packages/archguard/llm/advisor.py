"""Architecture Advisor foundation for generating AI-driven recommendations."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any


@dataclass
class Recommendation:
    """Structured architectural recommendation."""

    title: str
    description: str
    severity: str  # "critical", "high", "medium", "low"
    expected_impact: str
    priority_score: int  # 0-100 internal ranking


class AdvisorProvider(abc.ABC):
    """Abstract base class for LLM providers generating recommendations."""

    @abc.abstractmethod
    def generate_recommendations(self, context: str) -> list[Recommendation]:
        """Generate architectural recommendations based on the provided context."""
        pass


class ArchitectureAdvisor:
    """Core service for analyzing ArchGuard results and generating recommendations."""

    def __init__(self, provider: AdvisorProvider) -> None:
        self.provider = provider

    def analyze(self, runs: list[dict[str, Any]]) -> list[Recommendation]:
        """Analyze historical runs and generate prioritized recommendations."""
        if not runs:
            return []

        latest_run = runs[-1]
        context = self._build_context(runs, latest_run)

        raw_recommendations = self.provider.generate_recommendations(context)

        return self._prioritize(raw_recommendations)

    def _build_context(self, runs: list[dict[str, Any]], latest: dict[str, Any]) -> str:
        """Construct a textual analysis context from the run data."""
        lines: list[str] = ["Architecture Analysis Context:", ""]

        # Health score & grade
        score = latest.get("score", 0.0)
        grade = latest.get("band", "UNKNOWN")
        lines.append(f"Current Health Score: {score:.2f} (Grade: {grade})")

        # Violations
        violations = latest.get("violations", [])
        lines.append(f"Active Violations: {len(violations)}")
        if violations:
            for v in violations[:10]:  # Limit to top 10 to avoid token bloat
                lines.append(
                    f"- [L{v.get('layer', '?')}] {v.get('module', 'Unknown')}: {v.get('message', '')} ({v.get('severity', 'low')})"
                )

        # Layer scores
        metrics = latest.get("metrics", {})
        layer_scores = metrics.get("layer_scores", {})
        if layer_scores:
            lines.append("Layer Scores:")
            for layer, l_score in layer_scores.items():
                lines.append(f"- Layer {layer}: {l_score:.2f}")

        # Fitness functions
        fitness = metrics.get("fitness_results", [])
        if fitness:
            lines.append("Fitness Function Results:")
            for f in fitness:
                status = "PASS" if f.get("passed", True) else "FAIL"
                lines.append(f"- {f.get('name', f.get('rule', 'Unknown'))}: {status}")
                if not f.get("passed", True) and f.get("evidence"):
                    lines.append(f"  Evidence: {f.get('evidence')}")

        # CI Failures
        ci_failures = latest.get("ci_failures", [])
        if ci_failures:
            lines.append("CI Failures:")
            for ci in ci_failures:
                lines.append(f"- {ci}")

        # Trend Context
        if len(runs) > 1:
            first_score = runs[0].get("score", 0.0)
            trend_dir = "improving" if score >= first_score else "declining"
            lines.append(
                f"Trend: {trend_dir} (from {first_score:.2f} to {score:.2f} over {len(runs)} runs)"
            )

        return "\n".join(lines)

    def _prioritize(
        self, recommendations: list[Recommendation]
    ) -> list[Recommendation]:
        """Rank recommendations by severity and expected impact/priority score."""
        severity_map = {"critical": 4000, "high": 3000, "medium": 2000, "low": 1000}

        def sort_key(r: Recommendation) -> int:
            base_score = severity_map.get(r.severity.lower(), 0)
            return base_score + r.priority_score

        # Sort descending (highest priority first), preserving stable order for equal scores
        return sorted(recommendations, key=sort_key, reverse=True)

    def ask_stream(self, question: str, context: str = "") -> Any:
        """Stream a response to an architecture question using Anthropic.

        Yields text chunks as they arrive from the Anthropic streaming API.
        Falls back to a single-chunk non-streaming response if Anthropic
        SDK is unavailable or the API key is not configured.
        """
        import os
        import logging
        from archguard.utils.content_filter import redact_secrets

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            yield "Anthropic API key not configured. Set ANTHROPIC_API_KEY to enable streaming."
            return

        redacted_q = redact_secrets(question)
        redacted_c = redact_secrets(context) if context else redacted_q.__class__(text="", redactions=[])

        if redacted_q.redactions or redacted_c.redactions:
            logging.warning(
                "Advisor stream question/context contained redacted secrets: %s",
                redacted_q.redactions + redacted_c.redactions,
            )

        safe_question = redacted_q.text
        safe_context = redacted_c.text

        try:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)

            system_prompt = (
                "You are an expert software architect. Answer questions about "
                "architecture with actionable, specific advice."
            )
            user_content = f"{safe_context}\n\nQuestion: {safe_question}" if safe_context else safe_question

            with client.messages.stream(
                model=os.environ.get("ARCHGUARD_PRIMARY_MODEL", "claude-sonnet-4-20250514"),
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            ) as stream:
                for text in stream.text_stream:
                    yield text
        except ImportError:
            yield "Anthropic SDK not installed. Run: pip install anthropic"
        except Exception as exc:
            yield f"Streaming error: {exc}"
