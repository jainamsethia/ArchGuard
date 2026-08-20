"""RemediationPlan engine for generating structured remediation plans from ArchGuard findings."""

from __future__ import annotations

import abc
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from archguard.llm.gemini import (
    GeminiAuthError,
    GeminiClient,
    GeminiError,
    resolve_api_key,
)


class RemediationUnavailableError(RuntimeError):
    """Raised when a remediation task list could not be generated (config or API error),
    as distinct from a successful call that legitimately found nothing to remediate.
    """

logger = logging.getLogger(__name__)

VALID_PRIORITIES = {"critical", "high", "medium", "low"}

# Whether a task's numeric target is one ArchGuard actually enforces, or the
# model's own recommendation. Defaults to SUGGESTED: an unlabelled or
# unrecognised value must never be promoted to "requirement", because the
# failure mode being guarded against is exactly a suggestion masquerading as one.
# Output budget for a remediation plan. Sized for the full capped selection
# (15 findings) rendered as tasks with descriptions and acceptance criteria, and
# overridable because Gemini's thinking models spend part of this budget before
# emitting any JSON.
_REMEDIATION_MAX_TOKENS: int = int(
    os.environ.get("ARCHGUARD_REMEDIATION_MAX_TOKENS", "8192")
)

TARGET_BASIS_REQUIREMENT = "archguard_requirement"
TARGET_BASIS_SUGGESTION = "suggestion"
VALID_TARGET_BASIS = {TARGET_BASIS_REQUIREMENT, TARGET_BASIS_SUGGESTION}

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
    # Whether this task's target is a threshold ArchGuard enforces, or the
    # model's own recommendation. See VALID_TARGET_BASIS.
    target_basis: str = TARGET_BASIS_SUGGESTION

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
        except Exception as exc:
            logger.exception("RemediationProvider raised an unexpected error: %s", exc)
            return RemediationPlan()

        unique_tasks = self._deduplicate(raw_tasks)
        return self._group(unique_tasks)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _labelled_metrics(violation: dict[str, Any]) -> str:
        """Render a violation's metrics as explicitly labelled facts.

        Splits the numbers into what ArchGuard *measured* and the limit it was
        *configured* with, because the model cannot tell them apart otherwise.
        Previously only the free-text message reached the prompt, so for
        duplication -- whose message carries the score but not the threshold --
        the model had no configured limit at all and supplied its own.
        """
        metrics = violation.get("metrics") or {}
        if not isinstance(metrics, dict) or not metrics:
            return ""

        measured_keys = ("fan_out", "duplication_score", "drift", "match_count")
        limit_keys = ("budget", "threshold")

        def fmt(value: Any) -> str:
            try:
                num = float(value)
            except (TypeError, ValueError):
                return str(value)
            return str(int(num)) if num == int(num) else f"{num:g}"

        measured = [f"{k}={fmt(metrics[k])}" for k in measured_keys if k in metrics]
        limits = [f"{k}={fmt(metrics[k])}" for k in limit_keys if k in metrics]

        parts = []
        if measured:
            parts.append(f"ArchGuard measured: {', '.join(measured)}")
        if limits:
            parts.append(f"ArchGuard's configured limit: {', '.join(limits)}")
        return "; ".join(parts)

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
            # No slice here: callers pass an already-ranked, already-capped set
            # (archguard.analysis.ranking.select_for_remediation). Truncating
            # again would silently drop the tail of a deliberate selection, and
            # would do it in arrival order rather than by severity.
            for v in violations:
                sev = v.get("severity", "low")
                layer = v.get("layer", "?")
                module = v.get("module", "Unknown")
                msg = v.get("message", "")
                lines.append(f"- [L{layer}] {module}: {msg} (severity={sev})")
                facts = self._labelled_metrics(v)
                if facts:
                    lines.append(f"    {facts}")

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
# Gemini Remediation Provider
# ---------------------------------------------------------------------------

_REMEDIATION_SYSTEM_PROMPT = """\
You are an expert Software Architect generating remediation plans from ArchGuard findings.
Based on the provided findings, produce a JSON object with a single key "tasks".

AUTHORITATIVE NUMBERS
The ONLY thresholds ArchGuard actually enforces are the ones given in the
findings below as "ArchGuard's configured limit". Treat every other number as
your own suggestion.
- Never present a threshold ArchGuard did not state as an ArchGuard requirement.
- Do not write phrases like "ArchGuard requires X" or "must be below X" unless X
  appears verbatim as a configured limit in the findings.
- You may still propose a stricter target; say plainly that it is your
  recommendation, e.g. "ArchGuard's limit is 0.10; consider aiming for 0.05".

Each task in the array MUST have exactly these fields:
- "title": Short, unique, descriptive task title (string)
- "description": Detailed implementation steps (string)
- "priority": One of "critical", "high", "medium", "low" (string)
- "effort_days": Estimated calendar days to complete (integer >= 1)
- "target_basis": "archguard_requirement" if this task's target is exactly a
  configured limit stated in the findings; "suggestion" for anything else,
  including your own stricter targets and tasks with no numeric target (string)
- "acceptance_criteria": A list of 2-4 measurable acceptance criteria strings

If there are no findings, return {"tasks": []}.
Do NOT wrap in markdown code fences.
"""


class GeminiRemediationProvider(RemediationProvider):
    """Calls Gemini to generate structured remediation tasks.

    Replaces the previous pair of providers (Anthropic primary, OpenAI
    fallback). With Gemini as ArchGuard's only provider there is nothing to
    select between, so provider choice is no longer a runtime decision.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._client = GeminiClient(
            api_key=api_key, model=model, base_url=base_url, timeout=timeout
        )

    def generate_tasks(self, context: str) -> list[RemediationTask]:
        if os.environ.get("ARCHGUARD_MOCK_LLM") == "1":
            return []

        try:
            raw_content, finish_reason = self._client.complete(
                context,
                system=_REMEDIATION_SYSTEM_PROMPT,
                max_tokens=_REMEDIATION_MAX_TOKENS,
                json_object=True,
            )
        except GeminiAuthError as exc:
            # Surfaced verbatim: the dashboard renders this string, and the user
            # needs to know it is a credentials problem, not an empty result.
            raise RemediationUnavailableError(str(exc)) from exc
        except GeminiError as exc:
            logger.exception("Gemini remediation call failed: %s", exc)
            raise RemediationUnavailableError(str(exc)) from exc

        return _parse_remediation_response(raw_content, finish_reason)

# ---------------------------------------------------------------------------
# Shared response parser
# ---------------------------------------------------------------------------


def _decode_failure_detail(raw: str, exc: json.JSONDecodeError, finish_reason: str) -> str:
    """Describe *why* a response failed to decode, at the point it failed.

    A bare "Expecting value: line 43 column 9 (char 2360)" says nothing about
    what was actually at char 2360, so every occurrence needed the raw text
    recovered by hand before it could be diagnosed. This puts the deciding
    evidence -- length, finish_reason, and the surrounding characters -- into
    the message itself.
    """
    length = len(raw)
    parts = [f"{length} chars"]

    if finish_reason:
        parts.append(f"finish_reason={finish_reason!r}")

    # The decoder failing at the very end of the input is the signature of a
    # response that stopped mid-structure rather than one containing bad syntax.
    at_end = exc.pos >= length - 1
    if finish_reason == "length":
        parts.append(
            "TRUNCATED: the model hit its output token limit -- raise "
            "ARCHGUARD_REMEDIATION_MAX_TOKENS (Gemini's thinking models spend "
            "part of this budget before emitting any JSON)"
        )
    elif at_end:
        parts.append("input ends at the failure point (likely truncated)")

    window = 90
    start = max(0, exc.pos - window)
    end = min(length, exc.pos + window)
    before = raw[start:exc.pos]
    after = raw[exc.pos:end]
    snippet = f"{before}>>>HERE>>>{after}"
    parts.append(f"near failure: ...{snippet!r}...")

    return " | ".join(parts)


def _parse_remediation_response(
    raw: str, finish_reason: str = ""
) -> list[RemediationTask]:
    """Parse the JSON response from either provider into RemediationTask list."""
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        detail = _decode_failure_detail(raw, e, finish_reason)
        msg = f"Failed to JSON-decode remediation response: {e} [{detail}]"
        # Full body at ERROR: without it the snippet alone is often not enough
        # to tell a truncation from a malformed field.
        logger.exception("%s\n--- RAW REMEDIATION RESPONSE ---\n%s\n--- END RAW ---", msg, raw)
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
        target_basis = str(item.get("target_basis", "")).strip().lower()

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

        # Fail closed: only an explicit, recognised "archguard_requirement"
        # earns that label. Missing, misspelled or invented values degrade to a
        # suggestion, so a model that ignores the field cannot have its own
        # target presented as something ArchGuard enforces.
        if target_basis not in VALID_TARGET_BASIS:
            if target_basis:
                logger.warning(
                    "Unrecognised target_basis %r; treating as a suggestion.",
                    target_basis,
                )
            target_basis = TARGET_BASIS_SUGGESTION

        result.append(
            RemediationTask(
                title=str(title),
                description=str(description),
                priority=priority,
                effort_days=effort_days,
                acceptance_criteria=acceptance_criteria,
                target_basis=target_basis,
            )
        )

    return result


# ---------------------------------------------------------------------------
# Convenience async function (Step 15)
# ---------------------------------------------------------------------------


async def generate_remediation_plan(
    violations: list[dict[str, Any]] | list[Any] | None = None,
    fitness_failures: list[dict[str, Any]] | None = None,
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
    if fitness_failures:
        findings["fitness_failures"] = fitness_failures

    try:
        # Gemini is the only provider, so there is nothing to select between --
        # the sole decision left is whether it is configured at all.
        if not resolve_api_key():
            raise RemediationUnavailableError(
                "Gemini is not configured. Set GEMINI_API_KEY to enable AI fix suggestions."
            )
        provider: RemediationProvider = GeminiRemediationProvider()
        engine = RemediationEngine(provider)
        plan = engine.plan(findings)
        tasks = [
            {
                "title": t.title,
                "description": t.description,
                "priority": t.priority,
                "effort_days": t.effort_days,
                "acceptance_criteria": t.acceptance_criteria,
                "target_basis": t.target_basis,
            }
            for t in plan.all_tasks
        ]
        return {"tasks": tasks}
    except RemediationUnavailableError:
        raise
    except Exception as exc:
        logger.exception("generate_remediation_plan failed: %s", exc)
        return {"tasks": []}
