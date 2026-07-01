import os
import hmac
import ipaddress
import logging
from fastapi import Request, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer(auto_error=False)

_ALWAYS_TRUSTED_HOSTS = frozenset(
    ["127.0.0.1", "localhost", "::1", "testclient", "testserver"]
)


def _get_trusted_proxy_ips() -> frozenset[str]:
    """Parse ARCHGUARD_TRUSTED_PROXY_IPS into a frozenset of IP strings.

    Accepts a comma-separated list of IPv4 or IPv6 addresses/CIDR ranges.
    Invalid entries are logged and skipped.

    Example: ARCHGUARD_TRUSTED_PROXY_IPS=10.0.0.1,172.16.0.0/12
    """
    raw = os.environ.get("ARCHGUARD_TRUSTED_PROXY_IPS", "").strip()
    if not raw:
        return frozenset()
    trusted: set[str] = set()
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            # Validates both plain IPs and CIDR networks
            ipaddress.ip_network(entry, strict=False)
            trusted.add(entry)
        except ValueError:
            logging.warning(
                "ARCHGUARD_TRUSTED_PROXY_IPS: invalid entry %r - skipped", entry
            )
    return frozenset(trusted)


def _real_client_ip(request: Request) -> str:
    """Return the real client IP, trusting X-Forwarded-For from known proxy IPs.

    If the direct connection comes from a trusted proxy IP (or CIDR range in
    ARCHGUARD_TRUSTED_PROXY_IPS), the leftmost entry of X-Forwarded-For is
    used as the real client IP. Otherwise, the direct connection IP is used.

    This prevents X-Forwarded-For spoofing from untrusted clients.
    """
    direct_ip = request.client.host if request.client else "unknown"
    trusted_proxies = _get_trusted_proxy_ips()

    if not trusted_proxies:
        return direct_ip

    # Check whether the direct connection is from a trusted proxy
    try:
        direct_addr = ipaddress.ip_address(direct_ip)
        for entry in trusted_proxies:
            network = ipaddress.ip_network(entry, strict=False)
            if direct_addr in network:
                # Trust X-Forwarded-For; take the leftmost (real client) IP
                forwarded_for = request.headers.get("X-Forwarded-For", "")
                if forwarded_for:
                    real_ip = forwarded_for.split(",")[0].strip()
                    return real_ip
    except ValueError:
        pass  # malformed direct IP; fall through

    return direct_ip


def check_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> None:
    """Enforce authentication for all protected endpoints.

    Accepts EITHER:
    1. Authorization: Bearer <token>  - for CLI / API clients
    2. archguard_session cookie       - for browser clients (set via /api/auth/login)

    If ARCHGUARD_DASHBOARD_TOKEN is not set, falls back to IP-based allow/deny.
    """
    token = os.environ.get("ARCHGUARD_DASHBOARD_TOKEN")
    if token:
        # Path 1: Bearer token (CLI / API clients)
        if credentials and hmac.compare_digest(credentials.credentials, token):
            return  # authenticated via Bearer

        # Path 2: Session cookie (browser clients)
        from archguard.dashboard._cookie_auth import validate_session_cookie
        cookie_value = request.cookies.get("archguard_session", "")
        if cookie_value and validate_session_cookie(cookie_value, token):
            return  # authenticated via cookie

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # No token configured - fall back to IP-based check
    client_host = _real_client_ip(request)
    if client_host in _ALWAYS_TRUSTED_HOSTS:
        return  # localhost always trusted
    try:
        if ipaddress.ip_address(client_host).is_loopback:
            return
    except ValueError:
        pass

    allow_remote = os.environ.get(
        "ARCHGUARD_DASHBOARD_ALLOW_REMOTE", ""
    ).lower() in ("1", "true")
    if not allow_remote:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Dashboard requires ARCHGUARD_DASHBOARD_TOKEN to be set for "
                "remote access. Set ARCHGUARD_DASHBOARD_TOKEN in your environment."
            ),
        )
    logging.warning(
        "Dashboard accessed from %s without token authentication! "
        "Set ARCHGUARD_DASHBOARD_TOKEN to secure this instance.",
        client_host,
    )
