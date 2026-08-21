"""Short-lived, job-scoped tokens for the SSE progress stream.

Session cookies moved to :mod:`archguard.dashboard._sessions`, where they are
keyed to a user id and stored in Redis. What remains here is the one credential
that legitimately travels in a URL: ``EventSource`` cannot set request headers,
so the stream endpoint has no other way to be authenticated.

Token format: ``<job_id>.<token_id>.<hmac-sha256>``. It grants read access to
the progress of a single job, for five minutes.
"""
import hashlib
import hmac
import os
import secrets
import time

_STREAM_TOKENS: dict[str, float] = {}
_STREAM_TTL = 300  # 5 minutes
#: Bounded so a caller cannot mint tokens until the process runs out of memory.
_STREAM_MAX = 10_000


def _sign(token: str, payload: str) -> str:
    """Return HMAC-SHA256(token, payload) as hex."""
    return hmac.new(token.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _evict_expired() -> None:
    """Drop expired stream tokens, and oldest-first if the map is still full."""
    now = time.time()
    for token in [t for t, ts in list(_STREAM_TOKENS.items()) if now - ts > _STREAM_TTL]:
        del _STREAM_TOKENS[token]
    while len(_STREAM_TOKENS) > _STREAM_MAX:
        del _STREAM_TOKENS[min(_STREAM_TOKENS, key=lambda t: _STREAM_TOKENS[t])]


def _issue_short_lived_stream_token(job_id: str) -> str:
    """Create a short-lived token scoped to one job's SSE progress stream."""
    token = os.environ.get("ARCHGUARD_DASHBOARD_TOKEN", "")
    if not token:
        return ""
    token_id = secrets.token_hex(16)
    sig = _sign(token, f"{job_id}:{token_id}")
    token_str = f"{job_id}.{token_id}.{sig}"
    _STREAM_TOKENS[token_str] = time.time()
    _evict_expired()
    return token_str


def validate_stream_token(token_str: str, token: str) -> bool:
    """Validate a stream token. Reusable until it expires.

    Deliberately not single-use. EventSource reconnects on its own whenever the
    connection drops -- that is its defining behaviour, not an error path -- and
    it reconnects to the same URL with the same token. Consuming the token on
    first use meant the first dropped packet turned every subsequent reconnect
    into a 401, and the client fell back to 1.5s polling for the rest of the
    job. Live progress degraded silently on the product's flagship interaction.

    The bound remains time: _STREAM_TTL (5 minutes), and the token is scoped to
    a single job id. What it grants is read access to the progress of a job
    whose id the holder already has.
    """
    try:
        job_id, token_id, sig = token_str.split(".", 2)
    except ValueError:
        return False

    expected = _sign(token, f"{job_id}:{token_id}")
    if not hmac.compare_digest(expected, sig):
        return False

    issued_at = _STREAM_TOKENS.get(token_str)
    if issued_at is None:
        return False
    if time.time() - issued_at > _STREAM_TTL:
        del _STREAM_TOKENS[token_str]
        return False

    return True

