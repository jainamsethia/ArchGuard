"""Deciding whether a webhook URL is safe to call, and pinning the answer.

A watched repository carries a webhook URL its owner chose, and our worker
connects to it unattended on a schedule. That makes it the one destination in
the product that somebody outside it picks, so it is the one that has to be
checked.

Checking it is not enough on its own. The obvious shape -- resolve the
hostname, look at the addresses, then hand the *hostname* to an HTTP client --
looks correct and is not: the client resolves the name again, and nothing
requires the second answer to match the first. A resolver the webhook's owner
controls can answer with a public address for the check and with
169.254.169.254 for the connection. That is DNS rebinding, and no amount of
additional checking of the hostname closes it, because the hostname was never
the thing that was wrong.

So `validate_webhook_url` returns the address it approved, and the caller
connects to that address rather than to the name. One resolution, one address,
no window in between. `SafeTarget` carries the two pieces of the original
hostname that still have to travel with the request -- the `Host` header, so
the receiver routes it to the right virtual host, and the TLS server name, so
the certificate is verified against the name the user typed rather than
against an IP no certificate is issued for.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import ParseResult, urlparse, urlunparse

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

#: Webhooks are HTTPS-only, so this is the port unless the URL says otherwise.
_DEFAULT_PORT = 443


@dataclass(frozen=True)
class SafeTarget:
    """A validated webhook destination, with the address decided.

    `request_url` is the original URL with the hostname replaced by the address
    that passed validation, which is what makes the check binding: an HTTP
    client given a literal address has nothing left to resolve.
    """

    #: The URL to give the HTTP client: the original, with the host replaced.
    request_url: str
    #: The hostname the user gave, kept for HTTP and TLS identity.
    host: str
    #: The address that was validated, and therefore the one contacted.
    ip: str
    port: int
    #: `Host`, when the URL was rewritten. The client's own default would name
    #: the address instead, and receivers behind a shared IP route on this.
    headers: dict[str, str]
    #: `sni_hostname`, so TLS is still verified against `host`. Without it the
    #: certificate is checked against the IP literal and every ordinary
    #: certificate fails.
    extensions: dict[str, str]


def validate_webhook_url(url: str) -> SafeTarget:
    """Check that a webhook URL is safe to call, and pin the address.

    Rejects anything that is not HTTPS, and anything that resolves -- or is
    written as -- an address that is not globally routable. Returns the
    destination to connect to.

    Raises:
        ValueError: with a message naming the hostname and the address it
            objected to, since the caller sees it as a configuration error.
    """
    if not url:
        raise ValueError("Webhook URL cannot be empty")

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Webhook URL must use HTTPS scheme")

    host = parsed.hostname
    if not host:
        raise ValueError("Invalid webhook URL: missing hostname")

    try:
        port = parsed.port or _DEFAULT_PORT
    except ValueError as exc:
        # urlparse defers the failure to attribute access, so a URL with a
        # nonsense port parses cleanly and explodes here.
        raise ValueError(f"Invalid webhook URL: bad port ({exc})") from exc

    if host.lower() == "localhost":
        raise ValueError("Webhook URL cannot point to localhost or loopback address")

    literal = _as_address(host)
    if literal is not None:
        # Already an address: there is nothing to resolve, so nothing can
        # change between here and the connection.
        _reject_if_unsafe(literal, host)
        return _target(url, parsed, host, literal, port, rewritten=False)

    host = _ascii_host(host)

    # Resolved exactly once. A second lookup would reopen the window this
    # function exists to close, even a well-meaning one.
    addresses = _resolve(host, port)
    for address in addresses:
        # Every answer, not just the one we intend to use. A name with several
        # records is served in whatever order the resolver likes, so accepting
        # it because the first address was public would make the outcome a
        # matter of luck -- and an attacker only needs to be lucky once.
        _reject_if_unsafe(address, host)

    # The first answer, which is the operating system's own preference:
    # getaddrinfo returns addresses in RFC 6724 order, so a host with no IPv6
    # route is already given its IPv4 records first. Pinning does cost the
    # client's fallback to the second address when the first will not accept a
    # connection -- an address cannot be pinned and left open at the same time.
    return _target(url, parsed, host, addresses[0], port, rewritten=True)


def _resolve(host: str, port: int) -> list[_IPAddress]:
    """Every distinct address `host` currently answers with."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(
            f"Webhook URL hostname could not be resolved: {host!r} ({exc})"
        ) from exc

    addresses: list[_IPAddress] = []
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError as exc:
            # Not reachable through a normal resolver, but an address we cannot
            # parse is an address we cannot judge, so it does not get a pass.
            raise ValueError(
                f"Webhook URL hostname resolved to an address that could not "
                f"be read: {host!r} -> {info[4][0]!r} ({exc})"
            ) from exc
        if address not in addresses:
            addresses.append(address)

    if not addresses:
        raise ValueError(f"Webhook URL hostname resolved to no addresses: {host!r}")
    return addresses


def _ascii_host(host: str) -> str:
    """The form of the hostname that TLS and the `Host` header can carry.

    An internationalised hostname reaches here as unicode. Handing it to the
    URL was previously enough -- the HTTP client punycoded it on the way out --
    but the SNI name and `Host` header are supplied directly now, and the TLS
    layer will not encode them. Converting once here keeps a webhook on an IDN
    domain working rather than failing on an encoding error at delivery time.
    """
    if host.isascii():
        return host
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(
            f"Invalid webhook URL: hostname is not a usable domain name ({host!r})"
        ) from exc


def _as_address(host: str) -> _IPAddress | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _unwrap(address: _IPAddress) -> _IPAddress:
    """Reduce an address to the one that decides where packets go.

    Two IPv6 forms carry an IPv4 address inside them, and the flags on the
    outer address describe the wrapper rather than the destination:
    `::ffff:127.0.0.1` is not `is_loopback`, and `2002:7f00:1::1` -- 6to4 for
    the same address -- reports as globally routable. Judging the address
    inside is the only way to get the same answer as the routing table.
    """
    if isinstance(address, ipaddress.IPv6Address):
        embedded = address.ipv4_mapped or address.sixtofour
        if embedded is not None:
            return embedded
    return address


def _reject_if_unsafe(address: _IPAddress, host: str) -> None:
    """Refuse anything that is not a public, unicast address.

    Stated as "must be global" rather than as a list of bad ranges, because
    the list is the part that goes stale: carrier-grade NAT and the 6to4
    encoding of loopback both passed a private/loopback/link-local/reserved
    check while being exactly what this is for. Multicast is called out
    separately -- Python considers it global, and it is not a destination we
    have any business sending an alert to.
    """
    effective = _unwrap(address)
    if not effective.is_global or effective.is_multicast:
        raise ValueError(
            f"Webhook URL cannot point to a private IP address, loopback, or "
            f"otherwise non-public address ({host} resolves to {address})"
        )


def _target(
    url: str,
    parsed: ParseResult,
    host: str,
    address: _IPAddress,
    port: int,
    *,
    rewritten: bool,
) -> SafeTarget:
    """Rebuild the URL around the validated address.

    When the URL already named an address there is nothing to rewrite, and no
    `Host` or SNI override either: overriding them with the literal is what the
    client would do anyway, and naming an IP in SNI breaks TLS for the rare
    receiver that does hold a certificate for its address.
    """
    if not rewritten:
        return SafeTarget(
            request_url=url,
            host=host,
            ip=str(address),
            port=port,
            headers={},
            extensions={},
        )

    literal = f"[{address}]" if isinstance(address, ipaddress.IPv6Address) else str(address)
    netloc = literal if parsed.port is None else f"{literal}:{parsed.port}"
    if parsed.username:
        # Credentials belong to the receiver, not to the address. Dropping them
        # while rewriting the host would turn every delivery into a 401.
        credentials = parsed.username
        if parsed.password:
            credentials = f"{credentials}:{parsed.password}"
        netloc = f"{credentials}@{netloc}"

    return SafeTarget(
        request_url=urlunparse(parsed._replace(netloc=netloc)),
        host=host,
        ip=str(address),
        port=port,
        headers={"Host": host},
        extensions={"sni_hostname": host},
    )
