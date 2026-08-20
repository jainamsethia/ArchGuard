"""Outbound delivery of trend alerts.

Preserved through the CLI removal: watched repositories re-scan on a schedule
and need somewhere to report a regression. That change of setting is what makes
the hardening below matter -- an interactive CLI run had a human watching it,
an unattended scheduled scan does not.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from archguard.alerting.trend_detector import TrendAlert
from archguard.utils.url_validator import validate_webhook_url

logger = logging.getLogger(__name__)

#: A webhook receiver that accepts a connection and never answers must not hang
#: the caller. Covers connect, read, write and pool acquisition.
WEBHOOK_TIMEOUT_SECONDS = 10.0


async def _validate(url: str) -> None:
    """Run the SSRF guard without blocking the event loop.

    ``validate_webhook_url`` resolves the hostname with ``socket.getaddrinfo``
    to catch DNS rebinding, which is a blocking call. Awaiting it inline stalled
    every other task in the process for the duration of a DNS lookup -- and for
    as long as the resolver takes when it is slow or hostile.
    """
    await asyncio.to_thread(validate_webhook_url, url)


def _check_response(url: str, response: httpx.Response) -> None:
    """Report a rejected delivery instead of discarding it.

    The response used to be thrown away, so a 401 from a rotated Slack webhook
    or a 500 from the receiver was indistinguishable from success -- alerts
    silently stopped arriving with nothing anywhere saying so.
    """
    if response.status_code >= 400:
        logger.warning(
            "Webhook delivery to %s failed with HTTP %d: %s",
            url,
            response.status_code,
            response.text[:200],
        )


async def send_slack_alert(webhook_url: str, alerts: list[TrendAlert]) -> None:
    if not alerts:
        return

    await _validate(webhook_url)

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "🏗 *ArchGuard Trend Alert*\n"
                + "\n".join(f"• {a.message}" for a in alerts),
            },
        }
    ]

    async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
        response = await client.post(webhook_url, json={"blocks": blocks})
    _check_response(webhook_url, response)


async def send_generic_webhook(url: str, alerts: list[TrendAlert]) -> None:
    if not alerts:
        return

    await _validate(url)

    payload = {
        "alerts": [
            {
                "message": a.message,
                "metric": a.metric,
                "module": a.module,
                "direction": a.direction,
                "delta": a.delta,
            }
            for a in alerts
        ]
    }

    async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
        response = await client.post(url, json=payload)
    _check_response(url, response)
