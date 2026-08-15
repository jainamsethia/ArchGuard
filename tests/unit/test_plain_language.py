"""Tests for the static plain-language violation explanations.

These are templates, not LLM output, so they can be asserted exactly. The point
of the layer is that someone with no software background can read the table, so
the tests check for the things that would quietly break that: jargon creeping
back in, a kind rendering blank, or the numbers going missing.
"""

from __future__ import annotations

import pytest

from archguard.analysis import violation_kinds
from archguard.analysis.plain_language import explain, explain_dict


def _v(kind, metrics=None, message="raw technical message"):
    return {"kind": kind, "metrics": metrics or {}, "message": message}


# ---------------------------------------------------------------------------
# Every kind renders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", violation_kinds.ALL_KINDS)
def test_every_known_kind_has_a_template(kind):
    e = explain(_v(kind))

    assert e.title and not e.title.endswith("."), "title should read as a heading"
    assert len(e.body) > 40, "body should actually explain, not label"
    assert e.title != explain(_v("definitely_not_a_kind")).title, (
        f"{kind} is falling through to the generic fallback"
    )


def test_unknown_kind_falls_back_without_rendering_blank():
    e = explain(_v("some_future_check", message="whatever it recorded"))

    assert e.title
    assert e.body
    assert "whatever it recorded" in e.technical_details


def test_missing_kind_and_message_still_renders_something():
    e = explain({})

    assert e.title
    assert e.body
    assert e.technical_details == "(no further detail recorded)"


# ---------------------------------------------------------------------------
# Real numbers reach the technical details
# ---------------------------------------------------------------------------


def test_fan_out_shows_the_real_numbers():
    e = explain(_v(violation_kinds.FAN_OUT, {"fan_out": 11.0, "budget": 10.0}))

    assert e.technical_details == "fan_out = 11, budget = 10"


def test_duplication_shows_score_threshold_and_matches():
    e = explain(
        _v(
            violation_kinds.DUPLICATION,
            {"duplication_score": 0.17, "threshold": 0.1, "match_count": 42.0},
        )
    )

    assert "duplication_score = 0.17" in e.technical_details
    assert "threshold = 0.10" in e.technical_details
    assert "matching_pairs = 42" in e.technical_details


def test_whole_numbers_render_without_a_trailing_decimal():
    e = explain(_v(violation_kinds.FAN_OUT, {"fan_out": 11.0, "budget": 10.0}))
    assert "11.0" not in e.technical_details


# ---------------------------------------------------------------------------
# Readability: the whole reason this layer exists
# ---------------------------------------------------------------------------

# Terms that assume the reader already knows the subject. If one of these ends
# up in a template we have shipped a technical explanation in a friendlier font.
_JARGON = [
    "fan-out", "fan_out", "coupling", "centroid", "cohesion", "module",
    "refactor", "dependency", "dependencies", "import", "namespace",
    "threshold", "budget", "repository", "commit", "codebase", "api",
]


@pytest.mark.parametrize("kind", violation_kinds.ALL_KINDS)
def test_explanations_avoid_jargon(kind):
    e = explain(_v(kind))
    prose = f"{e.title} {e.body}".lower()

    found = [word for word in _JARGON if word in prose]
    assert not found, f"{kind} explanation uses jargon: {found}"


@pytest.mark.parametrize("kind", violation_kinds.ALL_KINDS)
def test_numbers_stay_out_of_the_prose(kind):
    """Figures live in technical_details so the sentence stays readable."""
    e = explain(
        _v(kind, {"fan_out": 11.0, "budget": 10.0, "duplication_score": 0.17})
    )
    prose = f"{e.title} {e.body}"

    assert not any(ch.isdigit() for ch in prose), f"{kind} prose contains figures"


@pytest.mark.parametrize("kind", violation_kinds.ALL_KINDS)
def test_explanations_do_not_prescribe_a_fix(kind):
    """A template cannot see the code. Prescribing a fix is the LLM's job."""
    e = explain(_v(kind))
    prose = f"{e.title} {e.body}".lower()

    for verb in ("you should", "you must", "split ", "extract ", "rewrite "):
        assert verb not in prose, f"{kind} prescribes a fix: {verb!r}"


def test_explain_dict_is_json_serialisable_and_complete():
    d = explain_dict(_v(violation_kinds.FAN_OUT, {"fan_out": 11.0, "budget": 10.0}))

    assert set(d) == {"title", "body", "technical_details"}
    assert all(isinstance(x, str) for x in d.values())
