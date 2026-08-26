"""What this instance can actually do, for the interface to ask before offering.

The AI features degrade rather than fail: without ``GEMINI_API_KEY`` the
Advisor and remediation plans return an error per request. That is honest but
badly timed -- someone types a question, waits for a round trip, and is told the
feature was never going to work. The interface can know before it offers the
control.

Deliberately not a health check. ``/ready`` answers "should this instance take
traffic", and the AI features being off is not a reason to take an instance out
of rotation; it is a reason to grey out two buttons.
"""

from __future__ import annotations

import os
from typing import Any

from archguard.llm.gemini import ModelCheck, fallback_model, primary_model


class _State:
    """Holds the boot probe's result, if one was run.

    A class attribute rather than a module global so recording a result does
    not need `global`, which ruff flags and which makes the lifecycle harder to
    follow than it needs to be.
    """

    model_check: ModelCheck | None = None


_state = _State()


def record_model_check(result: ModelCheck | None) -> None:
    """Remember what the startup probe found, so callers need not repeat it."""
    _state.model_check = result


def ai_status() -> dict[str, Any]:
    """Whether the AI features will work, and if not, why.

    The reason is written for whoever has to fix it: a missing key names the
    variable, and a bad model id names the id. "AI unavailable" on its own
    sends an operator to the wrong place -- usually their credentials, when the
    model name is what is wrong.
    """
    # Mock mode serves canned AI responses without touching the API, which is
    # how the browser tests exercise the Advisor and remediation paths. The
    # features genuinely work in that mode, so reporting them unavailable would
    # be false -- and would disable the very controls those tests drive.
    if os.environ.get("ARCHGUARD_MOCK_LLM") == "1":
        return {"available": True, "reason": "", "mocked": True}

    if not os.environ.get("GEMINI_API_KEY", "").strip():
        return {
            "available": False,
            "reason": (
                "GEMINI_API_KEY is not set, so the AI Advisor and AI "
                "remediation plans are disabled."
            ),
        }

    check = _state.model_check
    # `checked=False` means the probe did not run or could not reach the API.
    # That says nothing about the models, and must not disable a deployment
    # that works.
    if check is not None and check.checked and not check.ok:
        return {
            "available": False,
            "reason": (
                f"The configured model(s) {', '.join(check.missing)} are not "
                "offered by the API, so AI requests would fail."
            ),
        }

    return {
        "available": True,
        "reason": "",
        "primary_model": primary_model(),
        "fallback_model": fallback_model(),
    }
