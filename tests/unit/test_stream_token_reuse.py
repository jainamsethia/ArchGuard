"""SSE stream tokens must survive an EventSource reconnect (C5).

``POST /api/v1/jobs`` hands the browser a stream URL carrying a short-lived
token, and the browser opens it with ``EventSource``. EventSource reconnects on
its own whenever the connection drops -- that is its defining behaviour, not an
error path -- and it reconnects to the same URL, with the same token.

The token was consumed on first successful validation, so the reconnect got a
401. The client then fell through to 1.5s polling for the rest of the job. On
the flagship interaction of the product, a single dropped packet silently
downgraded live progress, and nothing said so.

The token stays short-lived (5 minutes) and stays scoped to one job id; only
the single-use property is dropped. What it grants is read access to the
progress of a job whose id the holder already has.
"""

from __future__ import annotations

import time

import pytest

from archguard.dashboard import _cookie_auth
from archguard.dashboard._cookie_auth import (
    _issue_short_lived_stream_token,
    validate_stream_token,
)

_SECRET = "a" * 64
_JOB = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def dashboard_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", _SECRET)
    _cookie_auth._STREAM_TOKENS.clear()
    yield
    _cookie_auth._STREAM_TOKENS.clear()


def test_a_token_validates_more_than_once_within_its_ttl() -> None:
    """The C5 regression: an EventSource reconnect reuses the same URL."""
    token = _issue_short_lived_stream_token(_JOB)
    assert token

    assert validate_stream_token(token, _SECRET) is True, "initial connection"
    assert validate_stream_token(token, _SECRET) is True, "reconnect after a drop"
    assert validate_stream_token(token, _SECRET) is True, "and again"


def test_a_token_stops_working_after_its_ttl() -> None:
    """Reuse must not become unlimited lifetime."""
    token = _issue_short_lived_stream_token(_JOB)
    _cookie_auth._STREAM_TOKENS[token] = time.time() - (_cookie_auth._STREAM_TTL + 1)

    assert validate_stream_token(token, _SECRET) is False


def test_an_expired_token_is_evicted_not_merely_rejected() -> None:
    token = _issue_short_lived_stream_token(_JOB)
    _cookie_auth._STREAM_TOKENS[token] = time.time() - (_cookie_auth._STREAM_TTL + 1)

    validate_stream_token(token, _SECRET)
    assert token not in _cookie_auth._STREAM_TOKENS


def test_a_forged_signature_is_rejected() -> None:
    token = _issue_short_lived_stream_token(_JOB)
    job_id, token_id, _sig = token.split(".", 2)
    forged = f"{job_id}.{token_id}.{'0' * 64}"

    assert validate_stream_token(forged, _SECRET) is False


def test_a_token_signed_with_a_different_secret_is_rejected() -> None:
    token = _issue_short_lived_stream_token(_JOB)
    assert validate_stream_token(token, "b" * 64) is False


def test_an_unknown_token_is_rejected() -> None:
    """Correct shape and signature are not enough: it must have been issued."""
    token = _issue_short_lived_stream_token(_JOB)
    _cookie_auth._STREAM_TOKENS.clear()

    assert validate_stream_token(token, _SECRET) is False


def test_a_malformed_token_is_rejected() -> None:
    assert validate_stream_token("not-a-token", _SECRET) is False
    assert validate_stream_token("", _SECRET) is False


def test_no_token_is_issued_when_the_deployment_has_no_dashboard_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    assert _issue_short_lived_stream_token(_JOB) == ""
