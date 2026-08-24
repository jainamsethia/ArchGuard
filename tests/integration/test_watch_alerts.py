"""Regression alerts for watched repositories (P3-1).

``archguard.alerting`` has had trend detection and SSRF-guarded webhook
delivery for a while with no caller. These tests cover the caller.

The one that matters most is the ordering test. ``get_runs_for_repository``
returns runs newest-first; ``detect_trends`` reads ``runs[0]`` as the oldest in
the window. Handing the list over unreversed inverts every delta, which is C10
exactly -- the bug this package was already bitten by once, where an improving
repository was reported as degrading. It would look completely correct in code
review.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.db_fixtures import requires_postgres

_URL = "https://github.com/pallets/flask.git"
_HOOK = "https://hooks.slack.com/services/T000/B000/xxx"


@pytest.fixture()
def delivered(monkeypatch: pytest.MonkeyPatch) -> list[list[Any]]:
    """Capture what would have been sent, and configure a destination."""
    sent: list[list[Any]] = []

    async def _fake_slack(url: str, alerts: list[Any]) -> None:
        sent.append(alerts)

    monkeypatch.setenv("ARCHGUARD_SLACK_WEBHOOK", _HOOK)
    monkeypatch.delenv("ARCHGUARD_ALERT_WEBHOOK", raising=False)
    monkeypatch.setattr("archguard.alerting.webhooks.send_slack_alert", _fake_slack)
    return sent


async def _seed(scores: list[float], github_id: int = 93001, login: str = "alerts") -> dict[str, Any]:
    """A watched repository with one recorded run per score, oldest first."""
    from archguard.db.session import session_scope
    from archguard.db.store import (
        all_watched,
        create_job,
        persist_run,
        upsert_user,
        watch_repository,
    )

    async with session_scope() as s:
        user = await upsert_user(s, github_id=github_id, login=login)
        await watch_repository(s, user.id, _URL)
        user_id = user.id

    for i, score in enumerate(scores):
        async with session_scope() as s:
            job = await create_job(s, _URL, user_id=user_id)
            # user_id comes from the job; commit_sha is its own argument rather
            # than a payload key.
            await persist_run(
                s,
                job_id=job.id,
                payload={
                    "score": score,
                    "band": "PASS",
                    "module_scores": {},
                    "violations": [],
                    "layer_results": [],
                    "metrics": {},
                },
                commit_sha=f"{i:040d}",
            )

    async with session_scope() as s:
        return next(w for w in await all_watched(s) if w["repo_url"] == _URL)


@requires_postgres
@pytest.mark.asyncio
async def test_a_falling_health_score_is_reported(
    live_db: str, delivered: list[list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from archguard.worker.alerts import evaluate_and_alert

    monkeypatch.setenv("ARCHGUARD_ALERT_WINDOW", "5")
    entry = await _seed([90.0, 90.0, 90.0, 90.0, 55.0])

    assert await evaluate_and_alert(entry) >= 1
    assert len(delivered) == 1
    assert all(a.direction == "degrading" for a in delivered[0])


@requires_postgres
@pytest.mark.asyncio
async def test_a_recovering_repository_is_not_reported_as_degrading(
    live_db: str, delivered: list[list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordering trap, and C10 repeating.

    Runs come back newest-first and the detector reads index 0 as the oldest.
    Forget to reverse, and a repository climbing 55 -> 90 is delivered as a
    35-point degradation -- an alert that is not merely useless but actively
    misleading, about the team's own progress.
    """
    from archguard.worker.alerts import evaluate_and_alert

    monkeypatch.setenv("ARCHGUARD_ALERT_WINDOW", "5")
    entry = await _seed([55.0, 60.0, 70.0, 80.0, 90.0])

    assert await evaluate_and_alert(entry) == 0
    assert delivered == []


@requires_postgres
@pytest.mark.asyncio
async def test_the_same_regression_is_reported_only_once(
    live_db: str, delivered: list[list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trend stays in the window for several passes; the alert must not."""
    from archguard.db.session import session_scope
    from archguard.db.store import all_watched
    from archguard.worker.alerts import evaluate_and_alert

    monkeypatch.setenv("ARCHGUARD_ALERT_WINDOW", "5")
    entry = await _seed([90.0, 90.0, 90.0, 90.0, 55.0])

    assert await evaluate_and_alert(entry) >= 1
    assert len(delivered) == 1

    # Same data, next pass. Re-read the row so the dedup marker is current.
    async with session_scope() as s:
        refreshed = next(w for w in await all_watched(s) if w["repo_url"] == _URL)
    assert await evaluate_and_alert(refreshed) == 0
    assert len(delivered) == 1, "the identical regression was reported twice"


@requires_postgres
@pytest.mark.asyncio
async def test_nothing_is_sent_before_the_window_is_full(
    live_db: str, delivered: list[list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from archguard.worker.alerts import evaluate_and_alert

    monkeypatch.setenv("ARCHGUARD_ALERT_WINDOW", "5")
    entry = await _seed([90.0, 55.0])

    assert await evaluate_and_alert(entry) == 0
    assert delivered == []


@requires_postgres
@pytest.mark.asyncio
async def test_ordinary_drift_is_not_an_alert(
    live_db: str, delivered: list[list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from archguard.worker.alerts import evaluate_and_alert

    monkeypatch.setenv("ARCHGUARD_ALERT_WINDOW", "5")
    entry = await _seed([70.0, 70.0, 69.0, 70.0, 68.0])

    assert await evaluate_and_alert(entry) == 0
    assert delivered == []


@requires_postgres
@pytest.mark.asyncio
async def test_no_webhook_configured_does_no_work(
    live_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Detecting a trend nobody receives would still burn the dedup marker."""
    from archguard.worker.alerts import evaluate_and_alert

    monkeypatch.delenv("ARCHGUARD_SLACK_WEBHOOK", raising=False)
    monkeypatch.delenv("ARCHGUARD_ALERT_WEBHOOK", raising=False)
    monkeypatch.setenv("ARCHGUARD_ALERT_WINDOW", "5")
    entry = await _seed([90.0, 90.0, 90.0, 90.0, 55.0])

    assert await evaluate_and_alert(entry) == 0
    assert entry["last_alerted_sha"] is None


@requires_postgres
@pytest.mark.asyncio
async def test_a_failing_webhook_does_not_mark_the_alert_as_sent(
    live_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise a delivery outage silently swallows the one alert that mattered."""
    from archguard.db.session import session_scope
    from archguard.db.store import all_watched
    from archguard.worker.alerts import evaluate_and_alert

    async def _boom(url: str, alerts: list[Any]) -> None:
        raise RuntimeError("webhook receiver is down")

    monkeypatch.setenv("ARCHGUARD_SLACK_WEBHOOK", _HOOK)
    monkeypatch.delenv("ARCHGUARD_ALERT_WEBHOOK", raising=False)
    monkeypatch.setattr("archguard.alerting.webhooks.send_slack_alert", _boom)
    monkeypatch.setenv("ARCHGUARD_ALERT_WINDOW", "5")

    entry = await _seed([90.0, 90.0, 90.0, 90.0, 55.0])
    assert await evaluate_and_alert(entry) == 0

    async with session_scope() as s:
        refreshed = next(w for w in await all_watched(s) if w["repo_url"] == _URL)
    assert refreshed["last_alerted_sha"] is None, (
        "a failed delivery marked the regression as reported, so the retry "
        "next pass will be suppressed and the alert is lost for good"
    )
