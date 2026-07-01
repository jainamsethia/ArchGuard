"""Session-cookie authentication for browser clients.

Issues an HttpOnly, SameSite=Strict cookie after the user POSTs the
dashboard token to /api/auth/login. Subsequent same-origin API calls
carry the cookie automatically — no JavaScript header injection needed.

Cookie value format: <session_id>.<hmac-sha256>
where session_id is random 32-byte hex and the HMAC key is ARCHGUARD_DASHBOARD_TOKEN.
Sessions expire after ARCHGUARD_SESSION_COOKIE_TTL seconds (default: 86400 / 24h).
"""
import hmac
import hashlib
import os
import secrets
import time

COOKIE_NAME = "archguard_session"
_TTL = int(os.environ.get("ARCHGUARD_SESSION_COOKIE_TTL", "86400"))
# 24h default — controls auth cookie lifetime, NOT the AI Advisor session memory
# See also: ARCHGUARD_SESSION_TTL in _sessions.py (different subsystem)
# In-memory session store: session_id -> issued_at_unix_timestamp
# Single-instance deployments only. Multi-instance deployments should use Redis.
_SESSIONS: dict[str, float] = {}


def _sign(token: str, session_id: str) -> str:
    """Return HMAC-SHA256(token, session_id) as hex."""
    return hmac.new(
        token.encode(), session_id.encode(), hashlib.sha256
    ).hexdigest()


def issue_session(token: str) -> str:
    """Create a new session, store it, and return the signed cookie value."""
    session_id = secrets.token_hex(32)
    sig = _sign(token, session_id)
    _SESSIONS[session_id] = time.time()
    _evict_expired()
    return f"{session_id}.{sig}"


def validate_session_cookie(cookie_value: str, token: str) -> bool:
    """Return True iff the cookie value contains a valid, unexpired session."""
    try:
        session_id, sig = cookie_value.split(".", 1)
    except ValueError:
        return False

    # Verify HMAC first (constant-time)
    expected = _sign(token, session_id)
    if not hmac.compare_digest(expected, sig):
        return False

    # Check session exists and is not expired
    issued_at = _SESSIONS.get(session_id)
    if issued_at is None:
        return False
    if time.time() - issued_at > _TTL:
        del _SESSIONS[session_id]
        return False

    return True


def revoke_session(cookie_value: str) -> None:
    """Remove the session from the store (logout)."""
    try:
        session_id, _ = cookie_value.split(".", 1)
        _SESSIONS.pop(session_id, None)
    except ValueError:
        pass


def _evict_expired() -> None:
    """Remove expired sessions opportunistically (called on new login)."""
    now = time.time()
    expired = [sid for sid, ts in list(_SESSIONS.items()) if now - ts > _TTL]
    for sid in expired:
        del _SESSIONS[sid]
