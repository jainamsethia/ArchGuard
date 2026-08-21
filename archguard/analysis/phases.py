"""Named analysis phases, and how far through one is.

The SSE stream emitted opaque strings: a scrolling terminal log in front of an
operation that takes thirty seconds on a small repository and ten minutes on a
large one. It is the product's flagship interaction, and with no notion of
completion it mostly looked like it might have hung.

A percentage needs somewhere to come from, and guessing from elapsed time is
worse than nothing -- it reads as precise while being unrelated to the work. So
the phases are named, ordered, and each one carries the fraction of a typical
run that has finished by the time it starts.

These are estimates, and deliberately not equal. Layers 1 and 2 parse every
file with tree-sitter and finish quickly; layer 3 loads an embedding model and
embeds every module; layer 4 builds a FAISS index over the result. Spacing them
evenly would produce a bar that sprints to 80% and then sits still, which is
the specific way progress bars lose people's trust.
"""

from __future__ import annotations

from typing import Final

#: Phase name -> percent complete when that phase *begins*. Ordered.
PHASE_PERCENT: Final[dict[str, int]] = {
    "queued": 0,
    "cloning": 3,
    # Only when the repository has no .archguard.yml. Reads git history and
    # runs community detection, so it is not a quick step.
    "contract": 12,
    "scanning": 28,
    "layer1": 34,
    "layer2": 42,
    # The big one when ML is enabled: model load plus an embedding per module.
    "layer3": 55,
    "layer4": 78,
    "fitness": 92,
    "persisting": 96,
    "complete": 100,
}

#: What to show for a message that names no phase.
UNKNOWN_PHASE: Final[str] = ""


def percent_for(phase: str | None) -> int | None:
    """The percent a phase begins at, or None if it is not a known phase.

    None rather than 0: a message with no phase should leave the bar where it
    is, not send it back to the start.
    """
    if not phase:
        return None
    return PHASE_PERCENT.get(phase)


def is_terminal(phase: str | None) -> bool:
    return phase == "complete"


def clamp_monotonic(previous: int | None, candidate: int | None) -> int | None:
    """Never let the bar go backwards.

    Phases can be skipped (no ML extras means no layer 3 or 4) and, in the
    thread pool that runs layers 1 and 2, can report out of order. A bar that
    jumps back is read as a restart.
    """
    if candidate is None:
        return previous
    if previous is None:
        return candidate
    return max(previous, candidate)
