"""Outbound alert delivery (archguard.alerting.webhooks).

Preserved through the CLI removal for watched repositories. Three defects this
pins, all of which matter more once alerts fire unattended on a schedule rather
than at the end of an interactive CLI run:

* ``validate_webhook_url`` performs a blocking ``socket.getaddrinfo`` and was
  called directly from ``async def`` (D10), stalling the event loop on every
  delivery -- and on a slow or hostile resolver, for as long as the OS allows.
* The client had no timeout, so a webhook endpoint that accepts a connection
  and never responds hangs the caller indefinitely.
* The response was discarded, so a 401 or a 500 from the receiving service was
  indistinguishable from success.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Self

import pytest

from archguard.alerting.trend_detector import TrendAlert
from archguard.alerting.webhooks import send_generic_webhook, send_slack_alert

_URL = "https://hooks.example.com/services/T0/B0/xxx"


def _alert() -> TrendAlert:
    return TrendAlert(
        metric="health_score",
        module=None,
        direction="degrading",
        delta=12.5,
        window=10,
        message="Overall health degrading by 12.5 points over the last 10 runs",
    )


class _FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.text = "body"


class _FakeClient:
    """Records how it was constructed and what it was asked to post."""

    last_kwargs: dict[str, Any] = {}
    posts: list[tuple[str, dict[str, Any]]] = []
    status_code: int = 200

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_kwargs = kwargs

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        type(self).posts.append((url, kwargs))
        return _FakeResponse(type(self).status_code)


@pytest.fixture(autouse=True)
def fake_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeClient.posts = []
    _FakeClient.last_kwargs = {}
    _FakeClient.status_code = 200
    monkeypatch.setattr("archguard.alerting.webhooks.httpx.AsyncClient", _FakeClient)
    # The SSRF guard is exercised in test_url_validator.py; here it must not
    # make a real DNS query.
    monkeypatch.setattr(
        "archguard.alerting.webhooks.validate_webhook_url", lambda _url: None
    )


@pytest.mark.asyncio
async def test_no_alerts_sends_nothing() -> None:
    await send_slack_alert(_URL, [])
    await send_generic_webhook(_URL, [])
    assert _FakeClient.posts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", [send_slack_alert, send_generic_webhook])
async def test_a_timeout_is_configured(sender: Any) -> None:
    """Without one, an endpoint that accepts and never answers hangs forever."""
    await sender(_URL, [_alert()])
    assert "timeout" in _FakeClient.last_kwargs
    assert _FakeClient.last_kwargs["timeout"] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", [send_slack_alert, send_generic_webhook])
async def test_a_failing_response_is_logged(
    sender: Any, caplog: pytest.LogCaptureFixture
) -> None:
    _FakeClient.status_code = 500
    with caplog.at_level(logging.WARNING, logger="archguard.alerting.webhooks"):
        await sender(_URL, [_alert()])
    assert "500" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", [send_slack_alert, send_generic_webhook])
async def test_a_successful_response_is_not_logged_as_a_failure(
    sender: Any, caplog: pytest.LogCaptureFixture
) -> None:
    _FakeClient.status_code = 200
    with caplog.at_level(logging.WARNING, logger="archguard.alerting.webhooks"):
        await sender(_URL, [_alert()])
    assert caplog.text == ""


@pytest.mark.asyncio
async def test_url_validation_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D10: validate_webhook_url calls socket.getaddrinfo, which blocks.

    Resolving on the event loop stalls every other request in the process for
    the duration of a DNS lookup.
    """
    seen: dict[str, str] = {}
    main_thread = threading.current_thread().name

    def _record(_url: str) -> None:
        seen["thread"] = threading.current_thread().name

    monkeypatch.setattr("archguard.alerting.webhooks.validate_webhook_url", _record)

    await send_slack_alert(_URL, [_alert()])

    assert seen["thread"] != main_thread, (
        "validate_webhook_url ran on the event loop thread"
    )


@pytest.mark.asyncio
async def test_slack_payload_carries_every_alert_message() -> None:
    alerts = [_alert(), _alert()]
    await send_slack_alert(_URL, alerts)
    _url, kwargs = _FakeClient.posts[0]
    rendered = str(kwargs["json"])
    assert rendered.count("degrading by 12.5") == 2


@pytest.mark.asyncio
async def test_generic_payload_is_structured_not_prose() -> None:
    await send_generic_webhook(_URL, [_alert()])
    _url, kwargs = _FakeClient.posts[0]
    entry = kwargs["json"]["alerts"][0]
    assert entry["metric"] == "health_score"
    assert entry["direction"] == "degrading"
    assert entry["delta"] == pytest.approx(12.5)
