"""Gemini provider for the Architecture Advisor's non-streaming analyze() path.

Kept under its original module name so existing imports keep working, but it
now targets Gemini. The request/response handling was already the
OpenAI-compatible wire format, which is exactly what Gemini exposes -- the
substantive change is where the credentials and base URL come from.
"""

import json
import logging
import os

import httpx

from archguard.llm.advisor import AdvisorProvider, Recommendation
from archguard.llm.gemini import resolve_api_key, resolve_base_url, resolve_model

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an expert Software Architect analyzing architectural health data from ArchGuard.
Based on the provided context, generate a prioritized list of actionable recommendations.

You MUST return the output as a JSON array of objects. Do not wrap the JSON in markdown code blocks.
Each object MUST have exactly these keys:
- "title": A short, descriptive title (string)
- "description": Detailed actionable advice (string)
- "severity": Must be one of: "critical", "high", "medium", "low" (string)
- "expected_impact": Description of the positive impact of this fix (string)
- "priority_score": An integer from 0 to 100 indicating importance (number)

If the context shows no issues or violations, return an empty array: []
"""

def _mock_recommendations(context: str) -> list[Recommendation]:
    print(f"--- MOCK LLM PROMPT ---\n{context}\n--- END MOCK LLM PROMPT ---")
    return [
        Recommendation(
            title="Mock Recommendation",
            description="Mock description for testing",
            severity="low",
            expected_impact="Mock impact",
            priority_score=100
        )
    ]

class AdvisorUnavailableError(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)

class OpenAIAdvisorProvider(AdvisorProvider):
    """Gemini implementation of the Architecture Advisor provider."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = resolve_api_key(api_key)
        self.model = resolve_model(model)
        self.base_url = resolve_base_url(base_url)
        self.timeout = timeout

    def generate_recommendations(self, context: str) -> list[Recommendation]:
        """Generate recommendations by calling the Gemini API."""
        if os.environ.get("ARCHGUARD_MOCK_LLM") == "1":
            return _mock_recommendations(context)

        if not self.api_key:
            logger.error("GEMINI_API_KEY is missing. Cannot generate recommendations.")
            raise AdvisorUnavailableError("no_api_key")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ]

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            # Gemini's OpenAI-compatible endpoint supports JSON mode directly,
            # so this no longer needs to sniff for specific OpenAI model names.
            "response_format": {"type": "json_object"},
        }

        # When forcing JSON object, the system prompt must explicitly instruct returning a JSON object.
        # But we need an array. So we'll wrap it in a JSON object with a `recommendations` key.
        # Let's adjust the payload and prompt for compatibility with JSON mode.

        # Safer prompt for JSON mode compatibility:
        adjusted_prompt = SYSTEM_PROMPT.replace(
            "You MUST return the output as a JSON array of objects.",
            'You MUST return a JSON object with a single key "recommendations" containing an array of objects.',
        ).replace("return an empty array: []", 'return {"recommendations": []}')

        messages[0]["content"] = adjusted_prompt

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=headers
                )

            response.raise_for_status()
            data = response.json()

            raw_content = data["choices"][0]["message"]["content"]
            return self._parse_response(raw_content)

        except httpx.TimeoutException:
            logger.error("Timeout occurred while calling Gemini API.")
            raise AdvisorUnavailableError("api_error", detail="Timeout occurred while calling Gemini API.")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                retry_after = e.response.headers.get("Retry-After", "")
                wait_msg = f" Retry after {retry_after}s." if retry_after else ""
                logger.error("Rate limit exceeded while calling Gemini API.%s", wait_msg)
                raise AdvisorUnavailableError(
                    "api_error",
                    detail=f"Gemini rate limit exceeded.{wait_msg}",
                )
            else:
                logger.error(
                    f"HTTP error {e.response.status_code} calling Gemini API: {e.response.text}"
                )
                raise AdvisorUnavailableError("api_error", detail=str(e))
        except httpx.RequestError as e:
            logger.error(f"Network error calling Gemini API: {e}")
            raise AdvisorUnavailableError("api_error", detail=str(e))
        except (KeyError, IndexError) as e:
            logger.error(f"Malformed response from Gemini API: {e}")
            raise AdvisorUnavailableError("api_error", detail="Malformed response")

    def _parse_response(self, raw_content: str) -> list[Recommendation]:
        """Parse and validate the JSON response into Recommendation objects."""
        if not raw_content.strip():
            return []

        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON response: {e}")
            return []

        # Extract array from the wrapping object
        recs_data = parsed.get("recommendations", [])
        if not isinstance(recs_data, list):
            logger.error("LLM response did not contain a 'recommendations' array.")
            return []

        recommendations = []
        for item in recs_data:
            if not isinstance(item, dict):
                continue

            title = item.get("title")
            description = item.get("description")
            severity = str(item.get("severity", "low")).lower()
            expected_impact = item.get("expected_impact")
            priority_score = item.get("priority_score")

            # Validation
            if not (title and description and expected_impact):
                logger.warning(
                    f"Skipping malformed recommendation missing required string fields: {item}"
                )
                continue

            if severity not in {"critical", "high", "medium", "low"}:
                severity = "medium"

            try:
                priority_score = int(priority_score or 0)
                priority_score = max(0, min(100, priority_score))
            except (TypeError, ValueError):
                priority_score = 50

            recommendations.append(
                Recommendation(
                    title=str(title),
                    description=str(description),
                    severity=severity,
                    expected_impact=str(expected_impact),
                    priority_score=priority_score,
                )
            )

        return recommendations
