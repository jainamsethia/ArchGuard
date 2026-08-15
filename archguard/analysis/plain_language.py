"""Plain-language explanations of violations, for readers who don't write code.

Static templates, not LLM output. The set of violation kinds is small and fixed,
and what each one *means* is the same in every repository -- only the numbers
change. Generating that text with a language model per run would spend money and
latency to re-derive a constant, and would let the wording drift between runs of
the same analysis.

Rules these follow, and the reason for each:

* No jargon. "Module", "coupling", "fan-out", "centroid" and "threshold" are all
  terms that assume the reader already knows the subject.
* Say what it means for the project, not what the metric is called.
* Never state the numbers in the explanation. They are shown as separate
  technical details, so the explanation stays readable and the exact figures
  stay available without being buried in a sentence.
* Never claim a cause or prescribe a fix. A template cannot see the code; a
  specific remediation is what the LLM path is for.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from archguard.analysis import violation_kinds


@dataclass(frozen=True)
class PlainExplanation:
    """A heading, a short body, and the raw numbers behind the finding."""

    title: str
    body: str
    technical_details: str


def _fmt(value: float) -> str:
    """Render a metric without trailing noise (11.0 -> '11', 0.17 -> '0.17')."""
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}"


def _details(pairs: list[tuple[str, float]]) -> str:
    return ", ".join(f"{label} = {_fmt(value)}" for label, value in pairs)


def explain(violation: dict[str, Any]) -> PlainExplanation:
    """Return a plain-language explanation for one violation."""
    kind = str(violation.get("kind") or "")
    metrics = {k: float(v) for k, v in (violation.get("metrics") or {}).items()}

    if kind == violation_kinds.FAN_OUT:
        return PlainExplanation(
            title="This part of the project depends on too many others",
            body=(
                "This part of the project relies on more of the other parts than "
                "the project's settings allow. The more things it relies on, the "
                "more places a change here can go wrong, and the harder it is to "
                "work on this part on its own."
            ),
            technical_details=_details(
                [
                    ("fan_out", metrics.get("fan_out", 0.0)),
                    ("budget", metrics.get("budget", 0.0)),
                ]
            ),
        )

    if kind == violation_kinds.DUPLICATION:
        return PlainExplanation(
            title="The same code appears in more than one place",
            body=(
                "Very similar code was found in two or more parts of the project. "
                "When code is copied, a fix applied to one copy is easy to forget "
                "in the others, so the same problem can come back later."
            ),
            technical_details=_details(
                [
                    ("duplication_score", metrics.get("duplication_score", 0.0)),
                    ("threshold", metrics.get("threshold", 0.0)),
                    ("matching_pairs", metrics.get("match_count", 0.0)),
                ]
            ),
        )

    if kind == violation_kinds.DEPENDENCY_CYCLE:
        return PlainExplanation(
            title="Two parts of the project depend on each other",
            body=(
                "Two or more parts of the project each need the other in order to "
                "work. That makes them difficult to change, test, or reuse "
                "separately, because neither one can stand on its own."
            ),
            technical_details=str(violation.get("message") or "").strip(),
        )

    if kind == violation_kinds.IMPORT_BOUNDARY:
        return PlainExplanation(
            title="This part of the project uses something it is not meant to",
            body=(
                "This part of the project reaches into another part that the "
                "project's own rules say it should not use directly. Rules like "
                "this are usually there to keep the pieces separable."
            ),
            technical_details=str(violation.get("message") or "").strip(),
        )

    if kind == violation_kinds.SEMANTIC_DRIFT:
        return PlainExplanation(
            title="This part of the project has changed in purpose",
            body=(
                "The code here now does something noticeably different from what "
                "it did when this part was first measured. That is not wrong by "
                "itself, but it can mean a part has quietly taken on a job it was "
                "never meant to do."
            ),
            technical_details=_details(
                [
                    ("drift", metrics.get("drift", 0.0)),
                    ("threshold", metrics.get("threshold", 0.0)),
                ]
            ),
        )

    # Fallback: an unknown kind, or a run persisted before `kind` existed.
    # Say only what is certainly true rather than guessing at the meaning.
    detail = str(violation.get("message") or "").strip()
    return PlainExplanation(
        title="ArchGuard flagged something in this part of the project",
        body=(
            "ArchGuard's checks flagged this as worth a look. The technical "
            "description below is the full detail it recorded."
        ),
        technical_details=detail or "(no further detail recorded)",
    )


def explain_dict(violation: dict[str, Any]) -> dict[str, str]:
    """``explain`` as a JSON-serialisable dict, for the API payload."""
    e = explain(violation)
    return {
        "title": e.title,
        "body": e.body,
        "technical_details": e.technical_details,
    }
