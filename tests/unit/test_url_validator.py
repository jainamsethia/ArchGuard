"""What counts as an address we are willing to have our infrastructure call.

The webhook URL on a watched repository is the one place in the product where
somebody else chooses a destination and our worker connects to it, unattended,
on a schedule. Everything here is about the set of addresses that reaches.

Two properties, separately:

* which addresses are refused -- the table below;
* that the refused set is decided about the address actually connected to,
  rather than about an address resolved once and then discarded, which is
  tests/unit/test_ssrf_pinning.py.

Name resolution is stubbed throughout, so these tests state what the validator
does rather than what the machine's DNS happened to answer.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

from archguard.alerting.trend_detector import TrendAlert
from archguard.alerting.webhooks import send_slack_alert
from archguard.utils.url_validator import validate_webhook_url


def _resolves_to(*addresses: str) -> Any:
    """A resolver that answers every name with `addresses`."""

    def _resolver(host: Any, port: Any = None, *_a: Any, **_k: Any) -> list[Any]:
        answers = []
        for address in addresses:
            if ":" in address:
                answers.append(
                    (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, port or 443, 0, 0))
                )
            else:
                answers.append(
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 443))
                )
        return answers

    return _resolver


@pytest.fixture()
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every hostname resolves to a public address unless a test says otherwise."""
    monkeypatch.setattr(socket, "getaddrinfo", _resolves_to("93.184.216.34"))


# --------------------------------------------------------------------------
# Shape of the URL
# --------------------------------------------------------------------------


def test_valid_webhook_url(public_dns: None) -> None:
    target = validate_webhook_url("https://example.com/webhook")
    assert target.ip == "93.184.216.34"


def test_invalid_scheme() -> None:
    with pytest.raises(ValueError, match="must use HTTPS scheme"):
        validate_webhook_url("http://hooks.slack.com/services/T0000/B0000")


def test_empty_url() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_webhook_url("")


def test_missing_hostname() -> None:
    with pytest.raises(ValueError, match="missing hostname"):
        validate_webhook_url("https:///webhook")


def test_a_hostname_that_does_not_resolve_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fails(*_a: Any, **_k: Any) -> list[Any]:
        raise OSError("NXDOMAIN")

    monkeypatch.setattr(socket, "getaddrinfo", _fails)
    with pytest.raises(ValueError, match="could not be resolved"):
        validate_webhook_url("https://nowhere.example/webhook")


def test_a_hostname_that_resolves_to_nothing_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty answer must not read as "nothing unsafe was found"."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: [])
    with pytest.raises(ValueError, match="no addresses"):
        validate_webhook_url("https://nowhere.example/webhook")


# --------------------------------------------------------------------------
# Addresses written literally into the URL
# --------------------------------------------------------------------------


#: Every one of these must be refused. The comment is the reason it is here,
#: not a restatement of the address.
UNSAFE_LITERALS = [
    pytest.param("192.168.1.100", id="ipv4-private-192.168"),
    pytest.param("10.0.0.5", id="ipv4-private-10"),
    pytest.param("172.16.0.1", id="ipv4-private-172.16"),
    pytest.param("127.0.0.1", id="ipv4-loopback"),
    pytest.param("0.0.0.0", id="ipv4-unspecified"),
    # The cloud instance metadata endpoint: the single most valuable address
    # an SSRF can reach on a hosted deployment.
    pytest.param("169.254.169.254", id="ipv4-link-local-metadata"),
    # Carrier-grade NAT. Routable inside a provider's network, and previously
    # allowed -- it is neither private nor loopback nor link-local.
    pytest.param("100.64.0.1", id="ipv4-cgnat"),
    pytest.param("255.255.255.255", id="ipv4-broadcast"),
    pytest.param("224.0.0.1", id="ipv4-multicast"),
    pytest.param("::1", id="ipv6-loopback"),
    pytest.param("fe80::1", id="ipv6-link-local"),
    pytest.param("fc00::1", id="ipv6-unique-local"),
    pytest.param("::", id="ipv6-unspecified"),
    pytest.param("ff02::1", id="ipv6-multicast"),
    # An IPv4 address wearing an IPv6 spelling. `is_loopback` is False on the
    # v6 object, so anything checking the flags without unwrapping first lets
    # it through -- and Python's own answer here has changed between versions.
    pytest.param("::ffff:127.0.0.1", id="ipv4-mapped-loopback"),
    pytest.param("::ffff:169.254.169.254", id="ipv4-mapped-metadata"),
    pytest.param("::ffff:10.0.0.1", id="ipv4-mapped-private"),
    # 6to4: the same trick in a different wrapper. 2002::/16 encodes an IPv4
    # address in the next 32 bits, and this one is 127.0.0.1. Python reports
    # it as globally routable, so it was allowed.
    pytest.param("2002:7f00:1::1", id="6to4-loopback"),
    pytest.param("2002:a9fe:a9fe::1", id="6to4-metadata"),
]


@pytest.mark.parametrize("address", UNSAFE_LITERALS)
def test_an_unsafe_address_written_into_the_url_is_refused(address: str) -> None:
    host = f"[{address}]" if ":" in address else address
    with pytest.raises(ValueError, match=r"non-public address|localhost or loopback"):
        validate_webhook_url(f"https://{host}/webhook")


def test_invalid_localhost() -> None:
    with pytest.raises(ValueError, match="localhost or loopback"):
        validate_webhook_url("https://localhost:8080/webhook")


def test_a_public_literal_address_is_allowed() -> None:
    """A URL that names an address directly needs no resolution at all."""
    target = validate_webhook_url("https://93.184.216.34/webhook")
    assert target.ip == "93.184.216.34"


# --------------------------------------------------------------------------
# Addresses arrived at by resolving a hostname
# --------------------------------------------------------------------------


@pytest.mark.parametrize("address", UNSAFE_LITERALS)
def test_an_unsafe_address_behind_a_hostname_is_refused(
    address: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same table, reached the way an attacker would reach it.

    A hostname is the point: nothing about `hooks.example.com` looks unsafe,
    and the only way to know is to resolve it and look at the answer.
    """
    monkeypatch.setattr(socket, "getaddrinfo", _resolves_to(address))
    with pytest.raises(ValueError, match="non-public address"):
        validate_webhook_url("https://hooks.example.com/webhook")


def test_a_hostname_written_as_a_number_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """`https://2130706433/` is 127.0.0.1 to the resolver and a hostname to us.

    Nothing string-shaped catches this; resolving it does.
    """
    monkeypatch.setattr(socket, "getaddrinfo", _resolves_to("127.0.0.1"))
    with pytest.raises(ValueError, match="non-public address"):
        validate_webhook_url("https://2130706433/webhook")


def test_one_unsafe_answer_among_several_refuses_the_whole_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name with several A records is answered in whatever order it likes.

    Accepting it because the first address was public would mean the outcome
    depends on which answer the resolver felt like putting first -- so a
    rebinding attack only has to be lucky, or patient.
    """
    monkeypatch.setattr(socket, "getaddrinfo", _resolves_to("93.184.216.34", "10.0.0.5"))
    with pytest.raises(ValueError, match="non-public address"):
        validate_webhook_url("https://hooks.example.com/webhook")


def test_an_unsafe_answer_is_refused_wherever_it_appears_in_the_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same again with the order reversed, so passing cannot depend on it."""
    monkeypatch.setattr(socket, "getaddrinfo", _resolves_to("10.0.0.5", "93.184.216.34"))
    with pytest.raises(ValueError, match="non-public address"):
        validate_webhook_url("https://hooks.example.com/webhook")


def test_several_safe_answers_are_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolves_to("93.184.216.34", "1.1.1.1"))
    target = validate_webhook_url("https://hooks.example.com/webhook")
    assert target.ip in {"93.184.216.34", "1.1.1.1"}


def test_a_dual_stack_hostname_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A public name with both A and AAAA records is the ordinary case."""
    monkeypatch.setattr(
        socket, "getaddrinfo", _resolves_to("93.184.216.34", "2606:4700:4700::1111")
    )
    assert validate_webhook_url("https://hooks.example.com/webhook").ip


def test_the_error_names_the_hostname_and_the_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """The message goes to whoever configured the watch.

    "rejected" alone is unactionable when the hostname looks fine and the
    reason is two resolutions away.
    """
    monkeypatch.setattr(socket, "getaddrinfo", _resolves_to("169.254.169.254"))
    with pytest.raises(ValueError, match=r"hooks\.example\.com.*169\.254\.169\.254"):
        validate_webhook_url("https://hooks.example.com/webhook")


# --------------------------------------------------------------------------
# What the rest of the URL survives
# --------------------------------------------------------------------------


def test_the_path_and_query_are_preserved(public_dns: None) -> None:
    """A Slack webhook is entirely path: lose it and the delivery 404s."""
    target = validate_webhook_url("https://example.com/services/T0/B0/xyz?a=1")
    assert target.request_url.endswith("/services/T0/B0/xyz?a=1")


def test_a_non_default_port_is_preserved(public_dns: None) -> None:
    target = validate_webhook_url("https://example.com:8443/webhook")
    assert ":8443" in target.request_url
    assert target.port == 8443


def test_credentials_in_the_url_are_preserved(public_dns: None) -> None:
    """Rewriting the host must not quietly drop the authentication with it.

    A receiver expecting basic auth would answer 401, and the alert would be
    lost to a change that was supposed to be about addresses.
    """
    target = validate_webhook_url("https://user:pass@example.com/webhook")
    assert "user:pass@" in target.request_url


def test_the_default_port_is_reported_even_when_the_url_omits_it(public_dns: None) -> None:
    assert validate_webhook_url("https://example.com/webhook").port == 443


def test_an_unparseable_port_is_refused(public_dns: None) -> None:
    with pytest.raises(ValueError, match="port"):
        validate_webhook_url("https://example.com:not-a-port/webhook")


def test_an_internationalised_hostname_is_punycoded(public_dns: None) -> None:
    """TLS and the `Host` header cannot carry unicode.

    The HTTP client used to do this on the way out, because it was handed the
    original URL. It is handed an address now, so the name has to arrive in a
    form the TLS layer will accept or a webhook on an IDN domain fails at
    delivery with an encoding error.
    """
    target = validate_webhook_url("https://bücher.example/webhook")

    assert target.host == "xn--bcher-kva.example"
    assert target.host.isascii()
    assert target.extensions["sni_hostname"].isascii()


# --------------------------------------------------------------------------
# The senders refuse before they connect
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_slack_alert_validates_url() -> None:
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
