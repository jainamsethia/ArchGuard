"""Structured analysis progress (P1-4), and the dead parameter behind it (C6).

The SSE stream emitted opaque strings, so a thirty-second-to-ten-minute
operation showed a scrolling log with no notion of completion -- on the
product's flagship interaction, that mostly reads as "it might have hung".

The percentage has to come from somewhere. Guessing from elapsed time is worse
than nothing: it looks precise while being unrelated to the work. So phases are
named and ordered, and these tests pin the properties a progress bar needs to
be trustworthy rather than the specific numbers, which are estimates and will
be tuned.
"""

from __future__ import annotations

import pytest

from archguard.analysis import phases


def test_phases_are_ordered():
    """A later phase never reports less progress than an earlier one."""
    values = list(phases.PHASE_PERCENT.values())
    assert values == sorted(values), phases.PHASE_PERCENT


def test_the_run_starts_at_zero_and_ends_at_one_hundred():
    assert phases.PHASE_PERCENT["queued"] == 0
    assert phases.PHASE_PERCENT["complete"] == 100


def test_phases_are_not_evenly_spaced():
    """Deliberately uneven, because the work is.

    Layers 1 and 2 parse with tree-sitter and finish quickly; layer 3 loads an
    embedding model and embeds every module. Even spacing produces a bar that
    sprints to 80% and then sits still, which is the specific way progress bars
    lose people's trust.
    """
    from itertools import pairwise

    values = list(phases.PHASE_PERCENT.values())
    gaps = {b - a for a, b in pairwise(values)}
    assert len(gaps) > 1, "evenly spaced phases would misrepresent the work"


def test_an_unknown_phase_leaves_the_bar_alone():
    """None, not zero: a message with no phase must not reset the bar."""
    assert phases.percent_for(None) is None
    assert phases.percent_for("") is None
    assert phases.percent_for("not-a-phase") is None


def test_progress_never_goes_backwards():
    """Phases are skipped without ML extras, and layers 1 and 2 run
    concurrently so they can report out of order. A bar that jumps back reads
    as a restart."""
    assert phases.clamp_monotonic(50, 40) == 50
    assert phases.clamp_monotonic(50, 60) == 60
    assert phases.clamp_monotonic(50, None) == 50
    assert phases.clamp_monotonic(None, 30) == 30
    assert phases.clamp_monotonic(None, None) is None


def test_every_phase_the_analysis_emits_is_known():
    """A phase the frontend cannot label is a phase that shows the wrong text.

    Pinned by reading the emit call sites rather than by listing them here, so
    adding one to the orchestrator without adding it to the map fails.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "archguard"
    emitted: set[str] = set()
    for path in root.rglob("*.py"):
        for match in re.finditer(r'phase=["\'](\w+)["\']', path.read_text(encoding="utf-8")):
            emitted.add(match.group(1))
        for match in re.finditer(r'await _emit\([^,]+,\s*["\'](\w+)["\']\)', path.read_text(encoding="utf-8")):
            emitted.add(match.group(1))

    unknown = emitted - set(phases.PHASE_PERCENT)
    assert not unknown, f"emitted but not in PHASE_PERCENT: {sorted(unknown)}"


def test_every_phase_has_a_label_in_the_frontend():
    """The server sends an identifier; the wording lives with the page's copy.

    A phase with no label renders as whatever was there before, so the bar
    would advance under a stale caption.
    """
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[2]
        / "archguard/dashboard/static/index.js"
    ).read_text(encoding="utf-8")
    for phase in phases.PHASE_PERCENT:
        assert f"{phase}:" in js, f"index.js has no label for phase {phase!r}"


# ------------------------------------------------------------------- C6


def test_skip_explanation_is_gone():
    """It was threaded through three layers and read by none.

    ``AnalysisOrchestrator.run(skip_explanation=...)`` passed it to
    ``_run_orchestrator``, which never looked at it; the code that produced L4
    explanations lived in the CLI. Meanwhile the startup banner told operators
    a missing GEMINI_API_KEY disabled "L4 LLM explanations" -- a feature the
    website has never had.
    """
    import ast
    from pathlib import Path

    # Parsed, not grepped: the parameter is *described* in a docstring
    # explaining why it went, and a text search cannot tell prose about a thing
    # from a use of it.
    root = Path(__file__).resolve().parents[2] / "archguard"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names = [a.arg for a in node.args.args + node.args.kwonlyargs]
                if "skip_explanation" in names:
                    offenders.append(f"{path.name}:{node.lineno} parameter")
            elif isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "skip_explanation":
                        offenders.append(f"{path.name}:{node.lineno} argument")

    assert not offenders, f"skip_explanation still in use: {offenders}"


def test_the_startup_banner_does_not_promise_llm_explanations():
    from pathlib import Path

    app_py = (
        Path(__file__).resolve().parents[2] / "archguard/dashboard/app.py"
    ).read_text(encoding="utf-8")
    banner = app_py.split("recommended = {", 1)[1].split("}", 1)[0]
    assert "L4 LLM explanations" not in banner
    assert "Advisor" in banner, "it should still name what the key does power"


# --------------------------------------------------- the callback reaches
#                                                       the orchestrator


def test_the_orchestrator_callback_is_no_longer_discarded():
    """`progress_callback=None` was hardcoded at the adapter's call site.

    Every per-layer message the orchestrator emits was therefore thrown away,
    and the stream showed only the four the adapter produced itself -- which is
    why the log went silent for the entire length of the analysis.
    """
    from pathlib import Path

    adapter = (
        Path(__file__).resolve().parents[2]
        / "archguard/dashboard/pipeline_adapter.py"
    ).read_text(encoding="utf-8")
    # The orchestrator call must forward a real callback. Asserted positively:
    # `progress_callback=None` still legitimately appears as the *default* in
    # run_analysis_on_repo's own signature, so its absence is the wrong thing
    # to check for.
    assert "progress_callback=on_progress" in adapter
    assert "SSE progress handled at the adapter level" not in adapter


@pytest.mark.parametrize(
    "phase", ["cloning", "contract", "scanning", "layer1", "layer3", "fitness"]
)
def test_each_named_phase_has_a_percent(phase):
    value = phases.percent_for(phase)
    assert value is not None and 0 <= value <= 100
