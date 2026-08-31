"""The address that passed SSRF validation must be the address contacted.

`validate_webhook_url` resolved the hostname and checked what it got back, and
then handed the *hostname* to httpx, which resolved it again. Two lookups, and
nothing tying the second answer to the first: a resolver under the webhook
owner's control could answer with a public address while being validated and
with 169.254.169.254 a moment later, and the request would go to the second
one. Checking the hostname harder does not fix that -- the check and the
connection have to agree on one address.

So these tests assert on *which address received the TCP connection*, using
real listening sockets rather than an assertion about what the code intended.
The only thing stubbed is name resolution, which is the mechanism being
attacked; everything below it is the operating system's.

`_Resolver` refuses to answer for any name the test did not set up, so a bug
that reaches for the network is a failure here rather than a silent success on
a machine with working DNS.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import threading
from typing import Any

import httpx
import pytest

from archguard.alerting.trend_detector import TrendAlert
from archguard.alerting.webhooks import send_generic_webhook, send_slack_alert
from archguard.utils.url_validator import validate_webhook_url

#: A hostname that exists only inside these tests. `.test` is reserved by
#: RFC 2606 precisely so it can never resolve for real.
HOST = "rebind.archguard.test"

#: Globally routable, so validation accepts it. Nothing listens here and
#: nothing is expected to: it is the address the request is supposed to be
#: pinned to, and the tests assert on where the request did *not* go.
PUBLIC_IP = "93.184.216.34"

URL = f"https://{HOST}/hook"

#: Captured before any test patches the name it lives under.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _alert() -> TrendAlert:
    return TrendAlert(
        metric="health_score",
        module=None,
        direction="degrading",
        delta=12.5,
        window=10,
        message="Overall health degrading",
    )


# --------------------------------------------------------------------------
# A resolver the test controls, standing in for one the attacker controls
# --------------------------------------------------------------------------


class _Listener:
    """A real TCP socket that records who connected to it.

    Deliberately not an HTTP server: the assertion is about the connection,
    not the exchange, and a TLS handshake against a socket with no certificate
    would fail before any HTTP happened anyway. Accepting and closing is
    enough to prove the address was reached.
    """

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.address = self._sock.getsockname()
        self.connections = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _peer = self._sock.accept()
            except OSError:
                return
            self.connections += 1
            conn.close()

    def close(self) -> None:
        self._stop.set()
        self._sock.close()
        self._thread.join(timeout=2)


class _Resolver:
    """Answers `HOST` differently each time it is asked.

    A DNS-rebinding attack in one object: the first answer is the one shown to
    whoever is checking, every answer after it is the one the attacker wants
    connected to. Any other name raises, so a code path that reaches for real
    DNS fails here instead of quietly succeeding on a machine that has it.
    """

    def __init__(self, answers: list[tuple[str, int]]):
        self._answers = list(answers)
        self.lookups: list[str] = []

    def __call__(self, host: Any, port: Any = None, *args: Any, **kwargs: Any) -> list[Any]:
        # anyio IDNA-encodes the name before resolving it, so a second lookup
        # arrives as bytes while the validator's arrives as str. Decoding
        # rather than comparing loosely: a resolver that failed to recognise
        # the name would raise below, which would look exactly like the fix
        # working.
        name = host.decode("ascii") if isinstance(host, bytes | bytearray) else str(host)
        self.lookups.append(name)

        if name != HOST:
            raise OSError(f"stubbed resolver: refusing to look up {name!r}")

        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", answer)]


@pytest.fixture()
def rebinding(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A hostname that validates as public and then rebinds to a live socket.

    The listener stands in for the internal service an SSRF is aimed at -- a
    metadata endpoint, an admin port, a database. It is a real socket on a real
    port, so "was it contacted" is answered by the operating system rather than
    by an assertion about intent.

    The port lives in the URL because that is the only way the rebound address
    can be made to land somewhere observable: anyio takes the address from DNS
    but the port from the URL.

    Delivery cannot succeed either way, and is not meant to. Once the address
    is pinned the request goes to `PUBLIC_IP`, which answers nothing -- one SYN
    leaves the machine and the connect times out. The timeout is shortened to
    keep that bounded; no assertion here depends on what becomes of it.
    """
    listener = _Listener()
    resolver = _Resolver(
        # Asked once, answer public, and pass validation. Asked again -- as a
        # second, unvalidated lookup would be -- answer with the target.
        answers=[(PUBLIC_IP, 443), listener.address],
    )
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    monkeypatch.setattr("archguard.alerting.webhooks.WEBHOOK_TIMEOUT_SECONDS", 1.0)
    try:
        yield {
            "listener": listener,
            "resolver": resolver,
            "url": f"https://{HOST}:{listener.address[1]}/hook",
        }
    finally:
        listener.close()


# --------------------------------------------------------------------------
# The invariant
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", [send_slack_alert, send_generic_webhook])
async def test_an_address_validation_never_approved_is_never_contacted(
    sender: Any, rebinding: Any
) -> None:
    """The invariant the whole task turns on, proven at the socket.

    Before the fix this listener received the connection: validation approved
    `PUBLIC_IP`, httpx was handed the hostname, resolved it for itself, and got
    the second answer. Nothing in between compared the two.
    """
    with pytest.raises(httpx.HTTPError):
        await sender(rebinding["url"], [_alert()])

    assert rebinding["listener"].connections == 0, (
        "the request reached an address that SSRF validation never approved: "
        "the hostname was resolved a second time and the second answer won"
    )


@pytest.mark.asyncio
async def test_the_hostname_is_resolved_once(rebinding: Any) -> None:
    """The root cause, stated directly.

    Two lookups of a name the caller does not control is the bug, whatever the
    second one happens to return on the day. One lookup means there is no
    second answer to disagree with.
    """
    with pytest.raises(httpx.HTTPError):
        await send_generic_webhook(rebinding["url"], [_alert()])

    assert rebinding["resolver"].lookups.count(HOST) == 1, (
        f"the hostname was resolved more than once: {rebinding['resolver'].lookups}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", [send_slack_alert, send_generic_webhook])
async def test_the_request_that_leaves_is_addressed_to_the_validated_address(
    sender: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: pinning has to actually reach the destination.

    A guard that refuses everything would satisfy the test above. This one
    asserts the request that leaves is aimed at the approved address, still
    identifies itself to the receiver by name, and still asks TLS to verify
    that name -- httpcore connects to the URL's host and verifies against
    `sni_hostname`, so those three together are the destination.
    """
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    monkeypatch.setattr(socket, "getaddrinfo", _Resolver([(PUBLIC_IP, 443)]))
    monkeypatch.setattr("archguard.alerting.webhooks.httpx.AsyncClient", _mock_client(handler))

    await sender(URL, [_alert()])

    request = sent[0]
    assert request.url.host == PUBLIC_IP, (
        f"the request went out addressed to {request.url.host!r}, which has to "
        "be resolved again by whoever connects"
    )
    assert request.headers["host"] == HOST, (
        "the receiver would see an IP as its Host header and route the "
        "delivery to the wrong virtual host"
    )
    assert request.extensions["sni_hostname"] == HOST
    assert request.url.path == "/hook"


def test_validation_reports_the_address_it_approved(rebinding: Any) -> None:
    """Validation has to hand back its answer, or the caller cannot use it.

    A validator that returns None can only ever be advisory -- whoever connects
    has to start from the hostname again.
    """
    target = validate_webhook_url(URL)

    assert target.ip == PUBLIC_IP
    assert target.host == HOST
    assert PUBLIC_IP in target.request_url
    assert target.headers.get("Host") == HOST, (
        "the receiver would see the wrong Host header and route the delivery "
        "to the wrong virtual host"
    )
    assert target.extensions.get("sni_hostname") == HOST, (
        "TLS would be verified against the IP literal instead of the hostname, "
        "which fails for every certificate that is not issued to an address"
    )


# --------------------------------------------------------------------------
# TLS must survive the pinning
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_certificate_verification_is_not_disabled_to_make_pinning_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinning the address must not be paid for with the certificate check.

    Putting an IP in the URL makes httpx verify the certificate against that
    IP unless the real hostname is supplied for SNI -- so the tempting fix is
    to turn verification off. This asserts the client is built with
    verification left alone.
    """
    built: dict[str, Any] = {}
    real = httpx.AsyncClient

    def _record(**kwargs: Any) -> httpx.AsyncClient:
        built.update(kwargs)
        return real(transport=httpx.MockTransport(lambda _r: httpx.Response(200)), **kwargs)

    monkeypatch.setattr("archguard.alerting.webhooks.httpx.AsyncClient", _record)
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver([(PUBLIC_IP, 443)]))

    await send_generic_webhook(URL, [_alert()])

    assert built.get("verify", True) is not False, (
        "certificate verification was switched off"
    )


# --------------------------------------------------------------------------
# Redirects
# --------------------------------------------------------------------------


def _mock_client(handler: Any) -> Any:
    """A real httpx client wired to a fake network.

    Real, so the redirect and response handling under test is httpx's own
    rather than a fake's idea of it -- a fake that ignores `follow_redirects`
    would pass whatever the code did.
    """

    def _factory(**kwargs: Any) -> httpx.AsyncClient:
        # `_REAL_ASYNC_CLIENT`, not `httpx.AsyncClient`: the name is patched to
        # this factory for the duration of the test, so reading it here would
        # call back into itself.
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

    return _factory


@pytest.fixture()
def public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver([(PUBLIC_IP, 443)]))


@pytest.mark.asyncio
async def test_a_redirect_is_not_followed(
    monkeypatch: pytest.MonkeyPatch, public_host: None
) -> None:
    """A validated destination must not be able to hand the request onward.

    Redirects are the way around address pinning: the address checked is the
    one connected to, and then the receiver replies "go to 169.254.169.254"
    and a following client obliges, resolving and connecting with nothing
    checking it. httpx does not follow redirects by default, which is why this
    was never exploitable -- but it is a default, and defaults get changed by
    someone who wants a redirect followed somewhere else.
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://169.254.169.254/latest/meta-data/"})

    monkeypatch.setattr("archguard.alerting.webhooks.httpx.AsyncClient", _mock_client(handler))

    await send_generic_webhook(URL, [_alert()])

    assert len(seen) == 1, f"the redirect was followed: {seen}"
    assert "169.254.169.254" not in " ".join(seen)


@pytest.mark.asyncio
async def test_a_redirect_is_reported_rather_than_silently_dropped(
    monkeypatch: pytest.MonkeyPatch, public_host: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Refusing to follow a redirect means the alert did not arrive.

    A 3xx is not a delivery. Treating it as one would put the watch back to
    sleep believing the owner had been told.
    """
    monkeypatch.setattr(
        "archguard.alerting.webhooks.httpx.AsyncClient",
        _mock_client(lambda _r: httpx.Response(302, headers={"Location": "https://elsewhere.example/"})),
    )

    with caplog.at_level(logging.WARNING, logger="archguard.alerting.webhooks"):
        await send_generic_webhook(URL, [_alert()])

    assert "302" in caplog.text


# --------------------------------------------------------------------------
# Response handling
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_hostile_reply_cannot_fill_the_log_or_the_worker(
    monkeypatch: pytest.MonkeyPatch, public_host: None, caplog: pytest.LogCaptureFixture
) -> None:
    """The receiver chooses the response body, and the receiver is the user.

    An error reply used to be read in full and then sliced for the log, so a
    webhook that answered 500 with a gigabyte answered it into the worker's
    memory. Bounded on the way in, not on the way out.
    """
    huge = b"A" * (8 * 1024 * 1024)
    monkeypatch.setattr(
        "archguard.alerting.webhooks.httpx.AsyncClient",
        _mock_client(lambda _r: httpx.Response(500, content=huge)),
    )

    with caplog.at_level(logging.WARNING, logger="archguard.alerting.webhooks"):
        await send_generic_webhook(URL, [_alert()])

    assert "500" in caplog.text
    assert len(caplog.text) < 4096, (
        f"a hostile receiver wrote {len(caplog.text)} bytes into our logs"
    )


@pytest.mark.asyncio
async def test_a_timeout_reaching_the_receiver_is_raised_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, public_host: None
) -> None:
    """The watch scheduler decides whether to retry, so it has to be told.

    `_deliver` in watch/service.py catches this and declines to record the
    alert as sent; swallowing it here would mark an alert nobody received as
    delivered and suppress the retry.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("receiver never answered")

    monkeypatch.setattr("archguard.alerting.webhooks.httpx.AsyncClient", _mock_client(handler))

    with pytest.raises(httpx.TimeoutException):
        await send_generic_webhook(URL, [_alert()])


@pytest.mark.asyncio
async def test_a_success_is_not_read_into_memory(
    monkeypatch: pytest.MonkeyPatch, public_host: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Nothing looks at the body of a 200, so nothing should download it."""
    monkeypatch.setattr(
        "archguard.alerting.webhooks.httpx.AsyncClient",
        _mock_client(lambda _r: httpx.Response(200, content=b"ok" * 100)),
    )

    with caplog.at_level(logging.WARNING, logger="archguard.alerting.webhooks"):
        await send_generic_webhook(URL, [_alert()])

    assert caplog.text == ""


# --------------------------------------------------------------------------
# The address family the pinning has to survive
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_ipv6_answer_is_pinned_and_bracketed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An IPv6 address has to go back into the URL in brackets.

    Without them the colons are read as a port separator and the request goes
    somewhere else entirely -- or, worse, the rewrite is skipped and the
    hostname is used after all.
    """
    v6 = "2606:4700:4700::1111"

    def resolver(host: Any, port: Any = None, *_a: Any, **_k: Any) -> list[Any]:
        if host == HOST:
            return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (v6, 443, 0, 0))]
        raise OSError(f"stubbed resolver: refusing to look up {host!r}")

    monkeypatch.setattr(socket, "getaddrinfo", resolver)

    target = validate_webhook_url(URL)

    assert target.ip == v6
    assert f"[{v6}]" in target.request_url, (
        f"an unbracketed IPv6 literal in {target.request_url!r}"
    )
    assert httpx.URL(target.request_url).host == v6
    assert ipaddress.ip_address(httpx.URL(target.request_url).host) == ipaddress.ip_address(v6)
