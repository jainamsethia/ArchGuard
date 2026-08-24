"""The SSRF guard on outbound webhook URLs.

``validate_webhook_url`` resolves the hostname with ``socket.getaddrinfo``,
because catching a name that points at a private address is the whole point --
a check on the literal string is defeated by DNS. That makes the *rejection*
cases hermetic (they are refused on the address or the scheme, before any
lookup) but made the acceptance case depend on live DNS, inside the unit suite.

It failed exactly that way: a connectivity blip produced
``ValueError: hostname could not be resolved`` and a red unit run that had
nothing to do with the code. A unit test that goes red when the network does is
not testing the thing it claims to.

The resolver is substituted below instead. That keeps what the test is actually
for -- a public address is accepted -- while dropping the dependency on
somebody else's DNS.
"""

import socket

import pytest

from archguard.alerting.trend_detector import TrendAlert
from archguard.alerting.webhooks import send_slack_alert
from archguard.utils.url_validator import validate_webhook_url


def test_valid_webhook_url(monkeypatch: pytest.MonkeyPatch):
    # A public address, as example.com would resolve to. Substituted rather
    # than looked up: see the module docstring.
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    # Should not raise
    validate_webhook_url("https://example.com/webhook")


def test_a_hostname_that_resolves_to_a_private_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
):
    """The reason the resolver is consulted at all.

    A public-looking hostname pointing at an internal address is the DNS
    rebinding case, and it is invisible to any check on the string alone.
    """
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))],
    )
    with pytest.raises(ValueError, match=r"private IP|link-local|metadata"):
        validate_webhook_url("https://totally-innocent.example.com/webhook")


def test_a_hostname_that_does_not_resolve_is_refused(monkeypatch: pytest.MonkeyPatch):
    def _boom(*a, **k):
        raise OSError("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    with pytest.raises(ValueError, match="could not be resolved"):
        validate_webhook_url("https://nonexistent.example.com/webhook")


def test_invalid_scheme():
    with pytest.raises(ValueError, match="must use HTTPS scheme"):
        validate_webhook_url("http://hooks.slack.com/services/T0000/B0000")


def test_invalid_private_ip():
    with pytest.raises(ValueError, match="cannot point to a private IP address"):
        validate_webhook_url("https://192.168.1.100/webhook")

    with pytest.raises(ValueError, match="cannot point to a private IP address"):
        validate_webhook_url("https://10.0.0.5/webhook")


def test_invalid_localhost():
    with pytest.raises(ValueError, match="cannot point to localhost or loopback"):
        validate_webhook_url("https://localhost:8080/webhook")

    with pytest.raises(ValueError, match="cannot point to localhost or loopback"):
        validate_webhook_url("https://127.0.0.1/webhook")


@pytest.mark.asyncio
async def test_send_slack_alert_validates_url():
    alerts = [
        TrendAlert(
            message="Test",
            metric="test",
            module="test",
            direction="up",
            delta=0.5,
            window=10,
        )
    ]
    with pytest.raises(ValueError, match="must use HTTPS scheme"):
        await send_slack_alert("http://example.com", alerts)
