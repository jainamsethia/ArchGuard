import ipaddress
import socket
from urllib.parse import urlparse


def validate_webhook_url(url: str) -> None:
    """Validate that a webhook URL is safe to call.

    Prevents SSRF by rejecting:
    - Non-HTTPS URLs
    - Localhost / loopback addresses
    - Private, link-local, or otherwise non-globally-routable IP ranges
      -- checked against the hostname's RESOLVED address(es), not just its
      literal spelling, to close DNS-rebinding bypasses.
    """
    if not url:
        raise ValueError("Webhook URL cannot be empty")

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Webhook URL must use HTTPS scheme")

    host = parsed.hostname
    if not host:
        raise ValueError("Invalid webhook URL: missing hostname")

    host_lower = host.lower()
    if host_lower == "localhost" or host_lower.startswith("127."):
        raise ValueError("Webhook URL cannot point to localhost or loopback address")

    try:
        literal_ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        _reject_if_unsafe(literal_ip, host)
        return

    # host is a hostname, not a literal IP -- resolve it and check every
    # address it points to, so a hostname that DNS-rebinds to a private
    # or loopback address is caught too.
    try:
        addr_infos = socket.getaddrinfo(host, None)
    except OSError:
        raise ValueError(f"Webhook URL hostname could not be resolved: {host!r}")

    resolved_ips = {ipaddress.ip_address(info[4][0]) for info in addr_infos}
    if not resolved_ips:
        raise ValueError(f"Webhook URL hostname resolved to no addresses: {host!r}")

    for resolved_ip in resolved_ips:
        _reject_if_unsafe(resolved_ip, host)


def _reject_if_unsafe(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, original_host: str) -> None:
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise ValueError(
            f"Webhook URL cannot point to a private IP address, loopback, or otherwise "
            f"non-public address ({original_host} resolves to {ip})"
        )
