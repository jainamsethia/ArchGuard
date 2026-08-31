"""Outbound delivery of trend alerts.

Preserved through the CLI removal: watched repositories re-scan on a schedule
and need somewhere to report a regression. That change of setting is what makes
the hardening below matter -- an interactive CLI run had a human watching it,
an unattended scheduled scan does not.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from archguard.alerting.trend_detector import TrendAlert
from archguard.utils.url_validator import SafeTarget, validate_webhook_url

logger = logging.getLogger(__name__)

#: A webhook receiver that accepts a connection and never answers must not hang
#: the caller. Covers connect, read, write and pool acquisition.
WEBHOOK_TIMEOUT_SECONDS = 10.0

#: Enough of a rejection to say why it was rejected. The receiver chooses this
#: body and the receiver is the user, so it is read up to a limit rather than
#: read in full and then trimmed for the log -- trimming afterwards still means
#: a webhook that answers 500 with a gigabyte answers it into the worker.
_MAX_BODY_BYTES = 2048

#: How much of that reaches the log line itself.
_MAX_LOGGED_CHARS = 200


async def _validate(url: str) -> SafeTarget:
    """Run the SSRF guard without blocking the event loop.

    ``validate_webhook_url`` resolves the hostname with ``socket.getaddrinfo``,
    which is a blocking call. Awaiting it inline stalled every other task in the
    process for the duration of a DNS lookup -- and for as long as the resolver
    takes when it is slow or hostile.

    The returned target is what makes the check binding rather than advisory:
    it names the address that was approved, so the request can be sent to that
    address instead of to a hostname the client would resolve for itself.
    """
    return await asyncio.to_thread(validate_webhook_url, url)


async def _read_capped(response: httpx.Response) -> str:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= _MAX_BODY_BYTES:
            break
    return b"".join(chunks)[:_MAX_BODY_BYTES].decode("utf-8", "replace")


async def _post(url: str, payload: dict[str, Any]) -> None:
    """Deliver one payload to a validated destination.

    Redirects are refused rather than followed. A redirect is the way around
    address pinning: the approved address is connected to, and then the
    receiver replies "now go to 169.254.169.254" and a following client obliges
    -- resolving and connecting to somewhere nothing checked. There is no
    webhook receiver that needs one, so the simpler behaviour is also the safe
    one; a 3xx is reported like any other refusal, because it means the alert
    did not arrive.
    """
    target = await _validate(url)

    async with (
        httpx.AsyncClient(
            timeout=WEBHOOK_TIMEOUT_SECONDS, follow_redirects=False
        ) as client,
        client.stream(
            "POST",
            target.request_url,
            json=payload,
            headers=target.headers,
            extensions=target.extensions,
        ) as response,
    ):
        if response.status_code >= 300:
            # The response used to be discarded, so a 401 from a rotated Slack
            # webhook or a 500 from the receiver was indistinguishable from
            # success -- alerts silently stopped arriving with nothing anywhere
            # saying so. Read only when there is something to say.
            body = await _read_capped(response)
            logger.warning(
                "Webhook delivery to %s failed with HTTP %d: %s",
                url,
                response.status_code,
                body[:_MAX_LOGGED_CHARS],
            )


async def send_slack_alert(webhook_url: str, alerts: list[TrendAlert]) -> None:
    if not alerts:
        return

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
    await _post(webhook_url, {"blocks": blocks})


async def send_generic_webhook(url: str, alerts: list[TrendAlert]) -> None:
    if not alerts:
        return

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
    await _post(url, payload)
