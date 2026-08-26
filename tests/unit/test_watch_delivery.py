"""Sending a regression alert, and the four ways that can go wrong quietly.

A watched repository is scanned unattended. Nobody is looking at the terminal
when the webhook 500s, nobody notices that a hostname now resolves to
169.254.169.254, and nobody times out the request by hitting Ctrl-C. Every one
of those is silent unless something here refuses.

The delivery seam is `service._deliver`, which returns whether the alert should
be considered sent. That boolean is what decides whether the durable
`last_alert_key` is written -- so getting it wrong in one direction sends the
same alert every day, and in the other loses the regression entirely.
"""

from __future__ import annotations

import asyncio

import pytest

from archguard.watch import service
from archguard.watch.regression import Regression

RUN = {"id": 3, "project_name": "demo", "repo_url": "https://github.com/x/demo"}
FOUND = Regression(
    kind="health_drop", summary="Health fell from 90 to 60.", alert_key="k" * 64
)


def _deliver(webhook):
    return asyncio.run(service._deliver(1, webhook, FOUND, RUN))


def test_a_watch_with_no_webhook_counts_as_delivered():
    """The regression is recorded and shown in the dashboard, which is where
    most people will look. Returning False would re-evaluate it as new on every
    later scan and never settle."""
    assert _deliver(None) is True
    assert _deliver("") is True


def test_a_successful_send_counts_as_delivered(monkeypatch):
    sent = []

    async def fake(url, alerts):
        sent.append((url, alerts))

    monkeypatch.setattr("archguard.alerting.webhooks.send_generic_webhook", fake)
    assert _deliver("https://example.com/hook") is True
    assert len(sent) == 1

    url, alerts = sent[0]
    assert url == "https://example.com/hook"
    assert alerts[0].message == "Health fell from 90 to 60."
    assert alerts[0].direction == "degrading", (
        "a regression was reported to the receiver as an improvement"
    )


def test_a_failed_send_is_not_treated_as_delivered(monkeypatch):
    """So the alert key is not recorded, and the next scan tries again.

    Recording it here would suppress the retry, and a missed regression is the
    exact failure this feature exists to prevent.
    """

    async def exploding(url, alerts):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("archguard.alerting.webhooks.send_generic_webhook", exploding)
    assert _deliver("https://example.com/hook") is False


def test_a_send_that_hangs_does_not_hang_the_worker(monkeypatch):
    """The receiver accepting a connection and never answering is the case a
    timeout exists for; without one the worker slot is held indefinitely."""

    async def hanging(url, alerts):
        raise TimeoutError("read timeout")

    monkeypatch.setattr("archguard.alerting.webhooks.send_generic_webhook", hanging)
    assert _deliver("https://example.com/hook") is False


@pytest.mark.parametrize(
    "unsafe",
    [
        "http://example.com/hook",   # plaintext
        "https://127.0.0.1/hook",    # loopback
        "https://169.254.169.254/",  # cloud metadata
        "https://10.1.2.3/hook",     # private range
    ],
)
def test_an_unsafe_destination_is_refused_at_send_time(unsafe, monkeypatch):
    """Not only when it was configured.

    A hostname that resolved to a public address at configuration time can be
    repointed at an internal one afterwards. The check at configuration is a
    courtesy so the user is told immediately; this one is the control, and it
    runs on every send.
    """
    requested = []

    async def tripwire(*args, **kwargs):
        requested.append(args)
        raise AssertionError("an HTTP request was made to an unsafe destination")

    monkeypatch.setattr("httpx.AsyncClient.post", tripwire)

    assert _deliver(unsafe) is False
    assert requested == []


def test_delivery_never_raises_into_the_analysis(monkeypatch):
    """The run is the product; the alert is a courtesy on top of it. An
    analysis that succeeded must not be recorded as failed because a webhook
    endpoint was down."""

    async def catastrophe(url, alerts):
        raise BaseExceptionGroup("several", [RuntimeError("a"), RuntimeError("b")])

    monkeypatch.setattr("archguard.alerting.webhooks.send_generic_webhook", catastrophe)
    assert _deliver("https://example.com/hook") is False
