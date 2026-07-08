"""RemediationPlan engine for generating structured remediation plans from ArchGuard findings."""

from __future__ import annotations

import abc
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

class RemediationUnavailableError(RuntimeError):
    """Raised when a remediation task list could not be generated (config or API error),
    as distinct from a successful call that legitimately found nothing to remediate.
    """

logger = logging.getLogger(__name__)

VALID_PRIORITIES = {"critical", "high", "medium", "low"}

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class RemediationTask:
    """A single actionable remediation task."""

    title: str
    description: str
    priority: str  # "critical" | "high" | "medium" | "low"
    effort_days: int  # estimated calendar days
    acceptance_criteria: list[str] = field(default_factory=list)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RemediationTask):
            return NotImplemented
        return self.title.strip().lower() == other.title.strip().lower()

    def __hash__(self) -> int:
        return hash(self.title.strip().lower())


@dataclass
class RemediationPlan:
    """Full remediation plan grouped by priority."""

    critical: list[RemediationTask] = field(default_factory=list)
    high: list[RemediationTask] = field(default_factory=list)
    medium: list[RemediationTask] = field(default_factory=list)
    low: list[RemediationTask] = field(default_factory=list)

    @property
    def all_tasks(self) -> list[RemediationTask]:
        return self.critical + self.high + self.medium + self.low

    @property
    def total(self) -> int:
        return len(self.all_tasks)


# ---------------------------------------------------------------------------
# Provider abstraction
# ---------------------------------------------------------------------------


class RemediationProvider(abc.ABC):
    """Abstract provider for generating remediation tasks from a context string."""

    @abc.abstractmethod
    def generate_tasks(self, context: str) -> list[RemediationTask]:
        """Return a list of remediation tasks for the given findings context."""


# ---------------------------------------------------------------------------
# Remediation engine
# ---------------------------------------------------------------------------

_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


class RemediationEngine:
    """Builds remediation plans from ArchGuard findings using a provider."""

    def __init__(self, provider: RemediationProvider) -> None:
        self.provider = provider

    def plan(self, findings: dict[str, Any]) -> RemediationPlan:
        """Generate a full remediation plan from structured ArchGuard findings.

        *findings* should contain any subset of:
          - ``score``: float health score
          - ``band``: health grade string
          - ``violations``: list of violation dicts
          - ``fitness_failures``: list of fitness function failure dicts
        """
        context = self._build_context(findings)
        if not context.strip():
            return RemediationPlan()

        try:
            raw_tasks = self.provider.generate_tasks(context)
        except RemediationUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("RemediationProvider raised an unexpected error: %s", exc)
            return RemediationPlan()

        unique_tasks = self._deduplicate(raw_tasks)
        return self._group(unique_tasks)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_context(self, findings: dict[str, Any]) -> str:
        """Convert structured findings into a text context for the provider."""
        lines: list[str] = ["Architecture Remediation Findings:", ""]

        score = findings.get("score")
        band = findings.get("band")
        if score is not None:
            lines.append(
                f"Health Score: {float(score):.2f} (Grade: {band or 'UNKNOWN'})"
            )

        violations: list[dict[str, Any]] = findings.get("violations", [])
        if violations:
            lines.append(f"\nArchitecture Violations ({len(violations)} total):")
            for v in violations[:15]:
                sev = v.get("severity", "low")
                layer = v.get("layer", "?")
                module = v.get("module", "Unknown")
                msg = v.get("message", "")
                lines.append(f"- [L{layer}] {module}: {msg} (severity={sev})")

        fitness_failures: list[dict[str, Any]] = findings.get("fitness_failures", [])
        if fitness_failures:
            lines.append(
                f"\nFitness Function Failures ({len(fitness_failures)} total):"
            )
            for ff in fitness_failures:
                name = ff.get("name") or ff.get("rule", "Unknown")
                evidence = ff.get("evidence", "")
                lines.append(f"- {name}: {evidence}" if evidence else f"- {name}")

        if not violations and not fitness_failures and score is None:
            return ""

        return "\n".join(lines)

    @staticmethod
    def _deduplicate(tasks: list[RemediationTask]) -> list[RemediationTask]:
        """Remove tasks with duplicate (case-insensitive) titles, keeping first occurrence."""
        seen: set[str] = set()
        result: list[RemediationTask] = []
        for task in tasks:
            key = task.title.strip().lower()
            if key not in seen:
                seen.add(key)
                result.append(task)
        return result

    @staticmethod
    def _group(tasks: list[RemediationTask]) -> RemediationPlan:
        """Partition tasks into priority buckets, sorted within each bucket."""
        plan = RemediationPlan()
        for task in tasks:
            p = (
                task.priority.lower()
                if task.priority.lower() in VALID_PRIORITIES
                else "medium"
            )
            getattr(plan, p).append(task)
        return plan


# ---------------------------------------------------------------------------
# OpenAI Remediation Provider
# ---------------------------------------------------------------------------

_REMEDIATION_SYSTEM_PROMPT = """\
You are an expert Software Architect generating remediation plans from ArchGuard findings.
Based on the provided findings, produce a JSON object with a single key "tasks".
Each task in the array MUST have exactly these fields:
- "title": Short, unique, descriptive task title (string)
- "description": Detailed implementation steps (string)
- "priority": One of "critical", "high", "medium", "low" (string)
- "effort_days": Estimated calendar days to complete (integer >= 1)
- "acceptance_criteria": A list of 2-4 measurable acceptance criteria strings

If there are no findings, return {"tasks": []}.
Do NOT wrap in markdown code fences.
"""


class OpenAIRemediationProvider(RemediationProvider):
    """Calls OpenAI to generate structured remediation tasks."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4-turbo")
        self.base_url = base_url or os.environ.get(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )
        self.timeout = timeout

    def generate_tasks(self, context: str) -> list[RemediationTask]:
        if not self.api_key:
            logger.warning("OPENAI_API_KEY missing. Cannot generate remediation tasks.")
            raise RemediationUnavailableError("OPENAI_API_KEY is not configured")

        messages = [
            {"role": "system", "content": _REMEDIATION_SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
            response.raise_for_status()
            data = response.json()
            raw_content = data["choices"][0]["message"]["content"]
            return self._parse(raw_content)

        except httpx.TimeoutException:
            logger.error("Timeout calling OpenAI for remediation.")
            raise RemediationUnavailableError("Timeout calling OpenAI for remediation.")
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if status_code == 429:
                logger.error("Rate limit exceeded calling OpenAI for remediation.")
                raise RemediationUnavailableError("Rate limit exceeded calling OpenAI for remediation.")
            else:
                msg = f"HTTP {status_code} calling OpenAI for remediation: {e.response.text}"
                logger.error(msg)
                raise RemediationUnavailableError(msg)
        except httpx.RequestError as e:
            msg = f"Network error calling OpenAI for remediation: {e}"
            logger.error(msg)
            raise RemediationUnavailableError(msg)
        except (KeyError, IndexError) as e:
            msg = f"Malformed OpenAI response for remediation: {e}"
            logger.error(msg)
            raise RemediationUnavailableError(msg)

    def _parse(self, raw: str) -> list[RemediationTask]:
        if not raw.strip():
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            msg = f"Failed to JSON-decode remediation response: {e}"
            logger.error(msg)
            raise RemediationUnavailableError(msg)

        tasks_data = data.get("tasks", [])
        if not isinstance(tasks_data, list):
            msg = "Remediation response 'tasks' is not a list."
            logger.error(msg)
            raise RemediationUnavailableError(msg)

        result: list[RemediationTask] = []
        for item in tasks_data:
            if not isinstance(item, dict):
                continue

            title = item.get("title")
            description = item.get("description")
            priority = str(item.get("priority", "medium")).lower()
            effort_days = item.get("effort_days")
            acceptance_criteria = item.get("acceptance_criteria", [])

            if not (title and description):
                logger.warning(
                    "Skipping remediation task missing title/description: %s", item
                )
                continue

            if priority not in VALID_PRIORITIES:
                priority = "medium"

            try:
                effort_days = max(1, int(effort_days or 0))
            except (TypeError, ValueError):
                effort_days = 1

            if not isinstance(acceptance_criteria, list):
                acceptance_criteria = []
            acceptance_criteria = [str(c) for c in acceptance_criteria if c]

            result.append(
                RemediationTask(
                    title=str(title),
                    description=str(description),
                    priority=priority,
                    effort_days=effort_days,
                    acceptance_criteria=acceptance_criteria,
                )
            )

        return result


# ---------------------------------------------------------------------------
# Convenience async function (Step 15)
# ---------------------------------------------------------------------------


async def generate_remediation_plan(
    violations: list[dict[str, Any]] | list[Any] | None = None,
) -> dict[str, Any]:
    """Generate a remediation plan from a list of violations.

    Parameters
    ----------
    violations : list
        A list of violation dicts (or any objects). Each dict may contain
        ``severity``, ``layer``, ``module``, ``message`` keys.

    Returns
    -------
    dict
        A JSON-serialisable dict with at least a ``"tasks"`` key containing
        a list of remediation task dicts.  Empty or *None* violations always
        returns ``{"tasks": []}``.
    """
    if not violations:
        return {"tasks": []}

    # Build a findings dict that the engine understands
    violation_dicts: list[dict[str, Any]] = []
    for v in violations:
        if isinstance(v, dict):
            violation_dicts.append(v)
        else:
            # Attempt to convert dataclass/object to dict
            violation_dicts.append(
                {
                    "severity": getattr(v, "severity", "medium"),
                    "layer": getattr(v, "layer", "?"),
                    "module": getattr(v, "module", "unknown"),
                    "message": getattr(v, "message", str(v)),
                }
            )

    findings: dict[str, Any] = {"violations": violation_dicts}

    try:
        provider = OpenAIRemediationProvider()
        engine = RemediationEngine(provider)
        plan = engine.plan(findings)
        tasks = [
            {
                "title": t.title,
                "description": t.description,
                "priority": t.priority,
                "effort_days": t.effort_days,
                "acceptance_criteria": t.acceptance_criteria,
            }
            for t in plan.all_tasks
        ]
        return {"tasks": tasks}
    except RemediationUnavailableError:
        raise
    except Exception as exc:
        logger.error("generate_remediation_plan failed: %s", exc)
        return {"tasks": []}
