"""What may authenticate the SSE progress stream, and what may not.

``EventSource`` cannot set request headers, so the stream endpoint is the one
place in the application where a credential legitimately travels in a query
string. That makes it the one place worth being precise about *which*
credential: a short-lived token scoped to a single job, never the operator
credential, which used to be accepted there -- and on every other endpoint too
(D2).
"""

from __future__ import annotations

import httpx
import pytest

from archguard.dashboard import _sessions
from archguard.dashboard.app import app

JOB_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
STREAM_URL = f"/api/v1/jobs/{JOB_ID}/stream"
SECRET = "c" * 64


@pytest.fixture(autouse=True)
def token_configured(monkeypatch):
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "ops-token-value")
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    _sessions.reset_sessions()
    yield
    _sessions.reset_sessions()


async def _get(**kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(STREAM_URL, **kwargs)


@pytest.mark.asyncio
async def test_no_credential_is_rejected():
    assert (await _get()).status_code == 401


@pytest.mark.asyncio
async def test_the_ops_token_is_not_accepted_in_the_query_string():
    """D2 regression.

    This used to return non-401: ``check_token`` compared ``?token=`` against
    ``ARCHGUARD_DASHBOARD_TOKEN`` directly, on every endpoint. A query string
    is the worst place for an admin credential -- proxy access logs, browser
    history, ``Referer`` headers -- and nothing about the stream needed it.
    """
    assert (await _get(params={"token": "ops-token-value"})).status_code == 401


@pytest.mark.asyncio
async def test_a_job_scoped_stream_token_is_accepted():
    """The credential that is *supposed* to work here.

    A 404 is the pass condition: the job does not exist, which is what we want
    to have got as far as discovering. A 401 would mean the token was refused.
    """
    from archguard.dashboard._cookie_auth import _issue_short_lived_stream_token

    stream_token = _issue_short_lived_stream_token(JOB_ID)
    assert stream_token, "a stream token requires ARCHGUARD_DASHBOARD_TOKEN to be set"
    response = await _get(params={"token": stream_token})
    assert response.status_code != 401, "a valid stream token was refused"


@pytest.mark.asyncio
async def test_a_stream_token_for_another_job_is_still_rejected_by_ownership():
    """Passing check_token is not the same as owning the job.

    The stream token proves the holder was given *a* job's id; the ownership
    check on the route is what decides whether they may read *this* one.
    """
    from archguard.dashboard._cookie_auth import _issue_short_lived_stream_token

    other = _issue_short_lived_stream_token("ffffffff-ffff-4fff-8fff-ffffffffffff")
    assert (await _get(params={"token": other})).status_code in (401, 404)


@pytest.mark.asyncio
async def test_a_garbage_query_token_is_rejected():
    assert (await _get(params={"token": "not-a-real-token"})).status_code == 401
