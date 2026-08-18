"""Tests that remediation targets are grounded in ArchGuard's real config.

Background: the model was observed proposing duplication targets of 0.05 and
0.03 phrased as ArchGuard requirements. 0.05 is a real number but belongs to the
*strict* profile, not the *ci* profile dashboard runs are graded against; 0.03
appears nowhere in the codebase. The cause was that _build_context sent only the
free-text violation message, and the duplication message carries the measured
score but not the threshold -- so the model had no configured limit to work from
and supplied its own.

These tests pin the two mitigations: the real configured limits are always in
the prompt as labelled facts, and any target the model does not explicitly mark
as a configured limit is recorded and rendered as a suggestion.
"""

from __future__ import annotations

import json

import pytest

from archguard.analysis import violation_kinds
from archguard.llm.remediation import (
    TARGET_BASIS_REQUIREMENT,
    TARGET_BASIS_SUGGESTION,
    RemediationEngine,
    RemediationProvider,
    RemediationTask,
    _parse_remediation_response,
)
from archguard.profiles.defaults import PROFILES


class _CapturingProvider(RemediationProvider):
    def __init__(self) -> None:
        self.context = ""

    def generate_tasks(self, context: str) -> list[RemediationTask]:
        self.context = context
        return []


def _context_for(violations: list[dict]) -> str:
    provider = _CapturingProvider()
    RemediationEngine(provider).plan({"score": 50.0, "violations": violations})
    return provider.context


def _violation(kind: str, metrics: dict, message: str = "msg", layer: int = 2) -> dict:
    return {
        "kind": kind,
        "metrics": metrics,
        "message": message,
        "module": "lib",
        "layer": layer,
        "severity": "high",
    }


# ---------------------------------------------------------------------------
# The real configured values always reach the model, labelled as such
# ---------------------------------------------------------------------------


def test_duplication_threshold_is_passed_as_a_labelled_limit():
    """The specific gap that let the model invent 0.05/0.03.

    The duplication message states the score but not the threshold, so without
    the metrics block the model never sees a configured limit for this kind.
    """
    ctx = _context_for(
        [
            _violation(
                violation_kinds.DUPLICATION,
                {"duplication_score": 0.11, "threshold": 0.10, "match_count": 42},
                message="duplication score 0.11 (matches found in: a <-> b)",
                layer=4,
            )
        ]
    )

    assert "ArchGuard's configured limit:" in ctx
    assert "threshold=0.1" in ctx
    assert "ArchGuard measured:" in ctx
    assert "duplication_score=0.11" in ctx


def test_fan_out_budget_is_passed_as_a_labelled_limit():
    ctx = _context_for(
        [
            _violation(
                violation_kinds.FAN_OUT,
                {"fan_out": 22, "budget": 10},
                message="fan_out=22 exceeds budget=10",
            )
        ]
    )

    assert "ArchGuard measured: fan_out=22" in ctx
    assert "ArchGuard's configured limit: budget=10" in ctx


def test_semantic_drift_threshold_is_passed_as_a_labelled_limit():
    ctx = _context_for(
        [
            _violation(
                violation_kinds.SEMANTIC_DRIFT,
                {"drift": 0.31, "threshold": 0.25},
                layer=3,
            )
        ]
    )

    assert "ArchGuard measured: drift=0.31" in ctx
    assert "ArchGuard's configured limit: threshold=0.25" in ctx


def test_measured_and_configured_values_are_never_conflated():
    """A measured value must not be presented as a limit, or vice versa."""
    ctx = _context_for(
        [
            _violation(
                violation_kinds.FAN_OUT,
                {"fan_out": 22, "budget": 10},
                message="fan_out=22 exceeds budget=10",
            )
        ]
    )
    measured_line = next(line for line in ctx.splitlines() if "ArchGuard measured" in line)

    before, after = measured_line.split("ArchGuard's configured limit:")
    assert "fan_out=22" in before and "budget=10" not in before
    assert "budget=10" in after and "fan_out=22" not in after


@pytest.mark.parametrize(
    "kind", [violation_kinds.IMPORT_BOUNDARY, violation_kinds.DEPENDENCY_CYCLE]
)
def test_kinds_without_numeric_limits_add_no_fabricated_ones(kind):
    """A cycle has no threshold; the prompt must not imply one exists."""
    ctx = _context_for([_violation(kind, {}, message="Cycle found: a -> b -> a")])

    assert "ArchGuard's configured limit:" not in ctx
    assert "ArchGuard measured:" not in ctx


def test_prompt_names_the_configured_limits_as_the_only_authority():
    from archguard.llm.remediation import _REMEDIATION_SYSTEM_PROMPT

    assert "ArchGuard's configured limit" in _REMEDIATION_SYSTEM_PROMPT
    assert "target_basis" in _REMEDIATION_SYSTEM_PROMPT
    # The instruction must cover the failure mode, not merely mention the field.
    assert "suggestion" in _REMEDIATION_SYSTEM_PROMPT.lower()


def test_the_profile_values_in_play_are_real_and_distinct():
    """Guards the premise: 0.05 is real but belongs to a different profile.

    If these ever coincide, the confusion this work addresses stops being
    detectable and this suite would silently stop testing anything.
    """
    assert PROFILES["strict"]["thresholds"]["max_duplication"] == 0.05
    assert PROFILES["ci"]["thresholds"]["max_duplication"] == 0.10
    assert (
        PROFILES["strict"]["thresholds"]["max_duplication"]
        != PROFILES["ci"]["thresholds"]["max_duplication"]
    )


# ---------------------------------------------------------------------------
# Parsing: both cases, and everything ambiguous fails closed
# ---------------------------------------------------------------------------


def _payload(**overrides) -> str:
    task = {
        "title": "Reduce duplication in lib",
        "description": "Extract the shared helper.",
        "priority": "high",
        "effort_days": 3,
        "acceptance_criteria": ["duplication below the configured limit"],
    }
    task.update(overrides)
    return json.dumps({"tasks": [task]})


def test_explicit_requirement_is_preserved():
    tasks = _parse_remediation_response(
        _payload(target_basis=TARGET_BASIS_REQUIREMENT)
    )
    assert tasks[0].target_basis == TARGET_BASIS_REQUIREMENT


def test_explicit_suggestion_is_preserved():
    tasks = _parse_remediation_response(_payload(target_basis=TARGET_BASIS_SUGGESTION))
    assert tasks[0].target_basis == TARGET_BASIS_SUGGESTION


def test_missing_target_basis_defaults_to_suggestion():
    """A model that ignores the field must not get its target promoted."""
    tasks = _parse_remediation_response(_payload())
    assert tasks[0].target_basis == TARGET_BASIS_SUGGESTION


@pytest.mark.parametrize(
    "value",
    ["", "requirement", "ArchGuard Requirement!", "mandatory", "true", "null", "123"],
)
def test_unrecognised_target_basis_falls_back_to_suggestion(value):
    tasks = _parse_remediation_response(_payload(target_basis=value))
    assert tasks[0].target_basis == TARGET_BASIS_SUGGESTION


def test_target_basis_is_case_and_whitespace_insensitive():
    tasks = _parse_remediation_response(
        _payload(target_basis="  ArchGuard_Requirement  ")
    )
    assert tasks[0].target_basis == TARGET_BASIS_REQUIREMENT


def test_default_on_the_dataclass_is_suggestion():
    task = RemediationTask("t", "d", "high", 1)
    assert task.target_basis == TARGET_BASIS_SUGGESTION


def test_target_basis_survives_serialisation_to_the_api():
    """The dashboard can only label what the endpoint actually sends."""
    from archguard.llm.remediation import RemediationPlan

    plan = RemediationPlan(
        high=[
            RemediationTask(
                "t", "d", "high", 1, [], target_basis=TARGET_BASIS_REQUIREMENT
            )
        ]
    )
    serialised = [
        {
            "title": t.title,
            "target_basis": t.target_basis,
        }
        for t in plan.all_tasks
    ]
    assert serialised[0]["target_basis"] == TARGET_BASIS_REQUIREMENT


# ---------------------------------------------------------------------------
# Decode-failure diagnostics
#
# A live failure read "Expecting value: line 43 column 9 (char 2360)" and needed
# the raw body recovered by hand before anyone could tell truncation from bad
# syntax. The message now has to carry that evidence itself.
# ---------------------------------------------------------------------------


def _decode_error(raw: str, finish_reason: str = "") -> str:
    from archguard.llm.remediation import RemediationUnavailableError

    with pytest.raises(RemediationUnavailableError) as exc:
        _parse_remediation_response(raw, finish_reason)
    return str(exc.value)


def test_truncated_response_is_named_as_truncated():
    """finish_reason='length' is the unambiguous signal; say so outright."""
    truncated = '{\n  "tasks": [\n    {\n      "title": "Reduce fan-out",\n      "description": "Refactor'

    msg = _decode_error(truncated, finish_reason="length")

    assert "TRUNCATED" in msg
    assert "ARCHGUARD_REMEDIATION_MAX_TOKENS" in msg, "must name the knob to turn"


def test_failure_message_reports_length_and_finish_reason():
    msg = _decode_error('{"tasks": [', finish_reason="length")

    assert "11 chars" in msg
    assert "finish_reason='length'" in msg


def test_failure_message_includes_a_snippet_around_the_failure():
    """The whole point: show what was actually at the failing position."""
    raw = '{"tasks": [{"title": "ok", "effort_days": }]}'

    msg = _decode_error(raw)

    assert ">>>HERE>>>" in msg
    assert "effort_days" in msg, "snippet should show the offending region"


def test_malformed_but_complete_response_is_not_blamed_on_truncation():
    """Bad syntax mid-document must not be mislabelled as a token-limit issue."""
    raw = '{"tasks": [{"title": "a", "priority": bogus}], "trailing": "value here"}'

    msg = _decode_error(raw, finish_reason="stop")

    assert "TRUNCATED" not in msg
    assert "input ends at the failure point" not in msg


def test_failure_at_end_of_input_is_flagged_even_without_finish_reason():
    """Some providers omit finish_reason; position still reveals truncation."""
    msg = _decode_error('{"tasks": [{"title": "a"')

    assert "likely truncated" in msg


def test_snippet_is_bounded_for_very_large_bodies():
    """The message goes into an HTTP response; it must not embed the whole body."""
    raw = '{"tasks": [' + ('{"title": "x"},' * 2000) + "bad]}"

    msg = _decode_error(raw)

    assert len(msg) < 1000, "snippet must stay bounded regardless of body size"


def test_valid_response_with_length_finish_reason_still_parses():
    """A complete JSON object that merely stopped at the limit is still usable."""
    tasks = _parse_remediation_response(_payload(), finish_reason="length")

    assert len(tasks) == 1
