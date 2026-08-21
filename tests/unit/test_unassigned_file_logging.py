"""One report per uncovered file per analysis, not one per lookup.

``_assign_file_to_module`` is called once per edge by the pipeline adapter's
payload builder, and once per *(module x edge)* by ``compute_fan_in`` -- which
is itself called once per module. A single file matching no module was
therefore logged dozens of times in one analysis. Measured on a four-file
repository (benjaminp/six): sixty-plus identical lines in a single run. On a
repository with hundreds of uncovered files that is thousands of lines, in a
worker process whose logs are the only way to debug it.

The fact is worth keeping, and is deliberately not lowered to debug: a file
matching no module means the contract's paths do not cover it, so it is
silently excluded from scoring. It is the repetition that carries nothing.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest

from archguard.analysis.coupling import (
    MAX_SUMMARISED_PATHS,
    _assign_file_to_module,
    collect_unassigned,
)

MODULE_PATHS = {"core": ["core/"]}


def _warnings(caplog) -> list[str]:
    # getMessage(), not record.message: the latter is the unformatted template
    # until a formatter has run, so interpolating it by hand double-formats.
    return [r.getMessage() for r in caplog.records]


def test_one_report_per_distinct_file_however_often_it_is_looked_up(caplog):
    """The regression. Fifty lookups of one file must not be fifty lines."""
    with caplog.at_level(logging.WARNING):
        with collect_unassigned():
            for _ in range(50):
                _assign_file_to_module("stray/a.py", MODULE_PATHS)

    mentions = [m for m in _warnings(caplog) if "stray/a.py" in m]
    assert len(mentions) == 1, f"expected one report, got {len(mentions)}"


def test_each_distinct_file_is_named_once(caplog):
    with caplog.at_level(logging.WARNING):
        with collect_unassigned():
            for _ in range(10):
                for name in ("a.py", "b.py", "c.py"):
                    _assign_file_to_module(f"stray/{name}", MODULE_PATHS)

    summary = "\n".join(_warnings(caplog))
    for name in ("a.py", "b.py", "c.py"):
        assert summary.count(f"stray/{name}") == 1, name


def test_the_report_is_a_single_line(caplog):
    """One line, not one per file. A hundred lines is still a wall of text."""
    with caplog.at_level(logging.WARNING):
        with collect_unassigned():
            for i in range(5):
                _assign_file_to_module(f"stray/f{i}.py", MODULE_PATHS)

    assert len(_warnings(caplog)) == 1


def test_the_report_says_how_many_and_why_it_matters(caplog):
    with caplog.at_level(logging.WARNING):
        with collect_unassigned():
            _assign_file_to_module("stray/a.py", MODULE_PATHS)
            _assign_file_to_module("stray/b.py", MODULE_PATHS)

    message = _warnings(caplog)[0]
    assert "2 file(s)" in message
    # The consequence, not just the fact. A reader who does not already know
    # what "not assigned to any module" implies learns nothing from the fact.
    assert "excluded from scoring" in message


def test_it_stays_at_warning_level(caplog):
    """Not lowered to debug. The information is useful; the volume was not."""
    with caplog.at_level(logging.DEBUG):
        with collect_unassigned():
            _assign_file_to_module("stray/a.py", MODULE_PATHS)

    assert [r.levelno for r in caplog.records] == [logging.WARNING]


def test_a_long_list_is_truncated(caplog):
    """A repository whose contract covers nothing must not produce a log line
    as long as its file count -- that is skipped rather than read."""
    with caplog.at_level(logging.WARNING):
        with collect_unassigned():
            for i in range(MAX_SUMMARISED_PATHS + 15):
                _assign_file_to_module(f"stray/f{i:03d}.py", MODULE_PATHS)

    message = _warnings(caplog)[0]
    assert f"{MAX_SUMMARISED_PATHS + 15} file(s)" in message
    assert "and 15 more" in message
    assert message.count("stray/") == MAX_SUMMARISED_PATHS


def test_nothing_is_reported_when_every_file_is_covered(caplog):
    with caplog.at_level(logging.WARNING):
        with collect_unassigned():
            _assign_file_to_module("core/a.py", MODULE_PATHS)

    assert _warnings(caplog) == []


def test_a_later_run_reports_the_same_file_again(caplog):
    """Deduplication is per run, not forever.

    A process-wide memo would mean the second analysis of a repository silently
    said nothing about the files the first one flagged -- which is how a
    long-lived worker stops reporting a real problem.
    """
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            with collect_unassigned():
                _assign_file_to_module("stray/a.py", MODULE_PATHS)

    assert len([m for m in _warnings(caplog) if "stray/a.py" in m]) == 2


def test_nested_scopes_produce_one_summary(caplog):
    """A stage opening its own scope must not start a second tally."""
    with caplog.at_level(logging.WARNING):
        with collect_unassigned():
            _assign_file_to_module("stray/a.py", MODULE_PATHS)
            with collect_unassigned():
                _assign_file_to_module("stray/b.py", MODULE_PATHS)

    messages = _warnings(caplog)
    assert len(messages) == 1
    assert "stray/a.py" in messages[0]
    assert "stray/b.py" in messages[0]


def test_outside_a_scope_the_warning_is_still_emitted(caplog):
    """A direct caller must not be silently deprived of the information."""
    with caplog.at_level(logging.WARNING):
        _assign_file_to_module("stray/a.py", MODULE_PATHS)

    assert any("stray/a.py" in m for m in _warnings(caplog))


def test_the_scope_reaches_worker_threads(caplog):
    """Layers 1 and 2 run in a ThreadPoolExecutor.

    `submit` does not copy the context, so without an explicit `copy_context`
    those threads see no scope and log per call -- which is most of where the
    duplication came from.
    """
    with caplog.at_level(logging.WARNING):
        with collect_unassigned():
            def lookup() -> None:
                for _ in range(20):
                    _assign_file_to_module("stray/a.py", MODULE_PATHS)

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(copy_context().run, lookup) for _ in range(2)]
                for f in futures:
                    f.result()

    assert len([m for m in _warnings(caplog) if "stray/a.py" in m]) == 1


def test_concurrent_analyses_do_not_share_a_tally():
    """A worker runs more than one analysis at a time in one process.

    A module-level set would let one job's file suppress another job's warning
    and mix two repositories into one summary. Each analysis gets its own
    because the scope lives in a ContextVar.
    """
    seen: list[set[str]] = []

    def one_analysis(name: str) -> None:
        with collect_unassigned() as tally:
            _assign_file_to_module(f"{name}/a.py", MODULE_PATHS)
            seen.append(set(tally))

    with ThreadPoolExecutor(max_workers=2) as pool:
        for name in ("repo1", "repo2"):
            pool.submit(copy_context().run, one_analysis, name).result()

    assert seen == [{"repo1/a.py"}, {"repo2/a.py"}]


@pytest.mark.parametrize("path", ["src/tests/b.py", "pkg/test/c.py"])
def test_nested_test_paths_are_skipped_not_reported(path, caplog):
    """They are deliberately excluded, so they are not a contract gap."""
    with caplog.at_level(logging.WARNING):
        with collect_unassigned():
            assert _assign_file_to_module(path, MODULE_PATHS) is None

    assert _warnings(caplog) == []


@pytest.mark.parametrize("path", ["tests/a.py", "test/c.py"])
def test_a_top_level_test_directory_is_reported(path, caplog):
    """Documenting existing behaviour rather than changing it.

    The skip matches ``/test/`` and ``/tests/`` with slashes on both sides, so
    a test directory at the repository root does not match and is reported as
    uncovered. Anchoring the check to path segments would silence that, but it
    would also stop assigning files to a module a contract legitimately
    declares under ``tests/`` -- which changes scoring, not just logging. Out of
    scope here; the report is one line per run either way.
    """
    with caplog.at_level(logging.WARNING):
        with collect_unassigned():
            assert _assign_file_to_module(path, MODULE_PATHS) is None

    assert len(_warnings(caplog)) == 1
    assert path in _warnings(caplog)[0]


def _coupling_fixture():
    """Six modules, one uncovered file importing into each of them.

    ``imported_module`` carries a submodule component on purpose: ``compute_fan_in``
    only reaches ``_assign_file_to_module`` after the import is shown to target
    the module under consideration, and a bare ``m0`` does not prefix-match
    ``m0/``. Without the ``.sub`` the branch under test is never entered and the
    test passes by not running.
    """
    from archguard.analysis.parser import ImportEdge

    module_paths = {f"m{i}": [f"m{i}/"] for i in range(6)}
    edges = [
        ImportEdge(
            source_file="stray/a.py",
            imported_module=f"m{i}.sub",
            is_stdlib=False,
            is_third_party=False,
            is_relative=False,
        )
        for i in range(6)
    ]
    return edges, module_paths


def test_without_a_scope_one_file_is_reported_many_times(caplog):
    """The defect, pinned. Delete the scope and this is what comes back.

    ``compute_fan_in`` is called once per module and looks up the source file of
    every edge, so one uncovered file costs (modules x edges) log lines.
    """
    from archguard.analysis.coupling import analyze_coupling

    edges, module_paths = _coupling_fixture()
    with caplog.at_level(logging.WARNING):
        analyze_coupling(edges, module_paths, {})

    mentions = [m for m in _warnings(caplog) if "stray/a.py" in m]
    assert len(mentions) > 1, (
        "the fixture is not reaching the unassigned branch, so the test below "
        "would pass without proving anything"
    )


def test_a_real_analysis_reports_each_file_once(caplog):
    """The same work inside a scope: one line."""
    from archguard.analysis.coupling import analyze_coupling

    edges, module_paths = _coupling_fixture()
    with caplog.at_level(logging.WARNING):
        with collect_unassigned(context="analysis"):
            analyze_coupling(edges, module_paths, {})

    mentions = [m for m in _warnings(caplog) if "stray/a.py" in m]
    assert len(mentions) == 1, (
        f"six modules x six edges produced {len(mentions)} reports"
    )


def test_the_pipeline_pass_is_one_scope_end_to_end():
    """`_run_analysis_sync` is the boundary, not each block inside it.

    It has two halves that both resolve every source file -- the orchestrator
    run, and the derived artifacts built afterwards -- so scoping them
    separately produced two identical summaries. The decorator spans both; the
    inner `with` joins it rather than starting a second tally.

    Asserted structurally because reproducing it needs a real clone: the
    function is decorated, and the decorator is `collect_unassigned`.
    """
    import ast
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "archguard/dashboard/pipeline_adapter.py"
    ).read_text(encoding="utf-8")

    fn = next(
        node
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.FunctionDef) and node.name == "_run_analysis_sync"
    )
    decorators = {
        d.func.id
        for d in fn.decorator_list
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
    }
    assert "collect_unassigned" in decorators, (
        "_run_analysis_sync must own the scope; without it the orchestrator "
        "run and the derived artifacts each emit their own summary"
    )
