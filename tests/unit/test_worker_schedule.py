"""How often the scheduled watch pass runs.

``archguard/worker/main.py`` is the arq entry point, imported by the worker
process and by nothing else -- it measured 0% coverage, which is how a module
whose only job is configuration ends up with a mistake nobody notices until a
deployment polls every watched repository once a minute.

``_watch_minutes`` is the one piece of logic in it: an interval in minutes, in
the environment, turned into the set of minutes-past-the-hour arq fires on. An
interval is asked for rather than a cron expression because the only thing an
operator wants to change is how often it runs, and a cron string invites getting
the other four fields wrong.
"""

from __future__ import annotations

import pytest


def _minutes(monkeypatch: pytest.MonkeyPatch, value: str | None) -> set[int]:
    from archguard.worker.main import _watch_minutes

    if value is None:
        monkeypatch.delenv("ARCHGUARD_WATCH_INTERVAL_MINUTES", raising=False)
    else:
        monkeypatch.setenv("ARCHGUARD_WATCH_INTERVAL_MINUTES", value)
    return _watch_minutes()


def test_the_default_is_hourly(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _minutes(monkeypatch, None) == {0}


@pytest.mark.parametrize(
    ("interval", "expected"),
    [
        ("15", {0, 15, 30, 45}),
        ("30", {0, 30}),
        ("20", {0, 20, 40}),
        ("5", {0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
    ],
)
def test_intervals_that_divide_an_hour_are_used_as_given(
    monkeypatch: pytest.MonkeyPatch, interval: str, expected: set[int]
) -> None:
    assert _minutes(monkeypatch, interval) == expected


def test_an_interval_that_does_not_divide_sixty_is_rounded_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gap between passes has to stay even.

    An interval of 25 taken literally fires at :00 and :25 and then waits 35
    minutes, so "every 25 minutes" would be true of one gap in three.
    """
    got = _minutes(monkeypatch, "25")
    ordered = sorted(got)
    # Wrap the first entry round to the next hour so the final gap is measured too.
    wrapped = [*ordered[1:], ordered[0] + 60]
    gaps = {(b - a) % 60 for a, b in zip(ordered, wrapped, strict=True)}
    assert len(gaps) == 1, f"uneven gaps between passes: {ordered}"
    assert got == {0, 30}


@pytest.mark.parametrize("interval", ["60", "90", "1440"])
def test_an_interval_of_an_hour_or_more_is_hourly(
    monkeypatch: pytest.MonkeyPatch, interval: str
) -> None:
    """arq's minute field cannot express "every 90 minutes"; hourly is the floor."""
    assert _minutes(monkeypatch, interval) == {0}


def test_zero_is_clamped_to_the_fastest_expressible_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not an error: 0 reads as "as often as possible", which is every minute."""
    assert _minutes(monkeypatch, "0") == set(range(60))


def test_every_schedule_is_a_valid_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    for interval in ("0", "1", "7", "13", "25", "59", "60", "3600"):
        got = _minutes(monkeypatch, interval)
        assert got, f"interval {interval} produced no schedule at all"
        assert all(0 <= m <= 59 for m in got), f"interval {interval} -> {sorted(got)}"


def test_the_pass_is_registered_as_a_cron_job() -> None:
    """A schedule nothing is attached to is just a number in a file."""
    from archguard.worker.main import WorkerSettings

    assert len(WorkerSettings.cron_jobs) == 1
    job = WorkerSettings.cron_jobs[0]
    # Not run on startup: a rolling deploy restarts every worker, and a pass on
    # each start would poll every watched repository once per restarting worker.
    assert getattr(job, "run_at_startup", False) is False
