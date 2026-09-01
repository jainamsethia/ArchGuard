import hmac
import ipaddress
import logging
import os

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

_ALWAYS_TRUSTED_HOSTS = frozenset(
    ["127.0.0.1", "localhost", "::1", "testclient", "testserver"]
)


def _get_trusted_proxy_ips() -> frozenset[str] | str:
    """Parse ARCHGUARD_TRUSTED_PROXY_IPS into a frozenset of IP strings.

    Accepts a comma-separated list of IPv4 or IPv6 addresses/CIDR ranges.
    Invalid entries are logged and skipped.

    Example: ARCHGUARD_TRUSTED_PROXY_IPS=10.0.0.1,172.16.0.0/12
    """
    raw = os.environ.get("ARCHGUARD_TRUSTED_PROXY_IPS", "").strip()
    if raw == "*":
        return "*"
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
            logger.warning(
                "ARCHGUARD_TRUSTED_PROXY_IPS: invalid entry %r - skipped", entry
            )
    return frozenset(trusted)


def _trusted_proxy_hops() -> int:
    """How many trusted proxies sit between the client and this process."""
    raw = os.environ.get("ARCHGUARD_TRUSTED_PROXY_HOPS", "1").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning(
            "ARCHGUARD_TRUSTED_PROXY_HOPS: %r is not an integer - using 1", raw
        )
        return 1


def _forwarded_client_ip(forwarded_for: str) -> str:
    """Pick the client IP out of an X-Forwarded-For chain, counting from the right.

    Every entry a *client* can put in this header is attacker-controlled: a
    proxy appends what it saw, it does not validate what was already there. So
    taking the leftmost entry -- the previous behaviour -- handed control of the
    value to the caller. With ARCHGUARD_TRUSTED_PROXY_IPS="*" (what render.yaml
    and railway.toml document), sending "X-Forwarded-For: <random>" produced a
    fresh rate-limit bucket on every request, which defeated the limiter on the
    LLM endpoints entirely.

    Only the rightmost entries are trustworthy, because only our own trusted
    proxies wrote them. With N trusted hops the client is the Nth from the right.

    Returns "" if the chain is shorter than the configured hop count, so the
    caller falls back to the direct peer address rather than to a value the
    client chose.
    """
    chain = [part.strip() for part in forwarded_for.split(",") if part.strip()]
    hops = _trusted_proxy_hops()
    if len(chain) < hops:
        logger.warning(
            "X-Forwarded-For %r has %d entries but ARCHGUARD_TRUSTED_PROXY_HOPS "
            "is %d; falling back to the direct peer address.",
            forwarded_for, len(chain), hops,
        )
        return ""
    return chain[-hops]


def _direct_client_ip(request: Request) -> str:
    """Return the IP of the peer actually connected to us, ignoring headers.

    Used for trust decisions that mean "this connection came from this
    machine". X-Forwarded-For must never influence those: a forwarded request
    did not originate locally by definition, so honouring the header there
    would let any client claim to be loopback.
    """
    return request.client.host if request.client else "unknown"


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
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            logger.warning(
                "Ignored X-Forwarded-For %r from %s because ARCHGUARD_TRUSTED_PROXY_IPS is not configured.",
                forwarded, direct_ip
            )
        return direct_ip

    # Check whether the direct connection is from a trusted proxy
    is_trusted = False
    if trusted_proxies == "*":
        is_trusted = True
    else:
        try:
            direct_addr = ipaddress.ip_address(direct_ip)
            for entry in trusted_proxies:
                network = ipaddress.ip_network(entry, strict=False)
                if direct_addr in network:
                    is_trusted = True
                    break
        except ValueError:
            pass  # malformed direct IP; fall through

    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if is_trusted:
        if forwarded_for:
            hop = _forwarded_client_ip(forwarded_for)
            if hop:
                return hop
    elif forwarded_for:
        logger.warning(
            "Ignored X-Forwarded-For %r from untrusted proxy %s.",
            forwarded_for, direct_ip
        )

    return direct_ip


def check_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> None:
    """Gate an endpoint on the operator credential or a signed-in session.

    This answers "may this request through?", which is a different and much
    weaker question than "whose data is this?". Every route that reads user
    data also depends on ``current_user``; this one remains for the endpoints
    that belong to no user -- health, and future admin surfaces.

    Accepts, in order:

    1. a valid session cookie -- any signed-in user;
    2. ``Authorization: Bearer <ARCHGUARD_DASHBOARD_TOKEN>`` -- operators;
    3. a short-lived, job-scoped stream token in ``?token=`` -- EventSource
       cannot set headers, so an SSE URL is the one place a credential may
       legitimately appear in a query string.

    If ARCHGUARD_DASHBOARD_TOKEN is not set, falls back to IP-based allow/deny
    for requests that carry no session.
    """
    # Path 1: a signed-in session, checked before anything looks at whether an
    # operator credential exists.
    #
    # It used to be checked *inside* the `if token:` branch below, which made a
    # user's authentication conditional on a setting that has nothing to do
    # with them. On a developer's machine that is invisible, because the peer is
    # loopback and the fallback allows everything. Behind a platform proxy the
    # peer is the proxy, the fallback allows nothing, and a correctly signed-in
    # user got 401 from every route on an instance that had passed every
    # startup check -- with a message telling the operator to set a token that
    # was never the reason the request was refused.
    #
    # Whether the deployment has an operator credential is not a fact about
    # whether this visitor is signed in, so it is no longer allowed to decide.
    from archguard.dashboard import _sessions

    if _sessions.resolve(request.cookies.get(_sessions.COOKIE_NAME, "")) is not None:
        return  # authenticated via a real session

    token = os.environ.get("ARCHGUARD_DASHBOARD_TOKEN")
    if token:
        # Path 2: Bearer token (CLI / API clients)
        if credentials and hmac.compare_digest(credentials.credentials, token):
            return  # authenticated via Bearer

        # Path 3: a job-scoped stream token (SSE EventSource clients).
        #
        # The raw ARCHGUARD_DASHBOARD_TOKEN used to be accepted here too (D2).
        # A query string is the worst place to put an admin credential: it
        # lands in proxy access logs, browser history and Referer headers, and
        # it was accepted on *every* endpoint, not just the stream. Only the
        # short-lived, single-job token is valid in a URL now.
        qs_token = request.query_params.get("token", "")
        if qs_token:
            from archguard.dashboard._cookie_auth import validate_stream_token

            if validate_stream_token(qs_token, token):
                return  # authenticated via short-lived stream token

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Nobody is signed in and no operator credential matched, so the only thing
    # left to judge by is where the request came from. This is what makes a
    # fresh checkout usable: no OAuth app, no token, and the dashboard works on
    # localhost. It is not a production path -- a hosted deployment sees its
    # proxy here, never loopback.
    #
    # Deliberately the DIRECT peer address, not the X-Forwarded-For-derived one:
    # with a trusted proxy configured (or "*"), a remote client could otherwise
    # send "X-Forwarded-For: 127.0.0.1" and be granted localhost trust.
    client_host = _direct_client_ip(request)
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
            # Names what the caller can actually do about it. This used to say
            # only "set ARCHGUARD_DASHBOARD_TOKEN", which is advice for the
            # operator rather than the visitor -- and since a session now
            # authenticates on its own, setting that token is no longer what
            # makes an ordinary request work.
            detail=(
                "Not authenticated. Sign in, or send an operator credential "
                "in an Authorization: Bearer header."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )
    logger.warning(
        "Dashboard accessed from %s (forwarded-for: %s) without token "
        "authentication! Set ARCHGUARD_DASHBOARD_TOKEN to secure this instance.",
        client_host,
        _real_client_ip(request),
    )
