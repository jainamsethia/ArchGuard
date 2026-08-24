"""The share endpoints and the public shared report (P3-2).

``/shared/{token}`` is the only data-bearing route in the application an
anonymous caller can reach, so the token is the entire credential. Most of what
is asserted here is about the boundary: what a stranger can reach with a link,
what they cannot reach without one, and what the page must not carry.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.db_fixtures import requires_postgres

_URL = "https://github.com/pallets/flask.git"


def _share(client: Any, job_id: str) -> Any:
    return client.post(f"/api/v1/runs/{job_id}/share")


def _token_from(resp: Any) -> str:
    return resp.json()["share_url"].rsplit("/", 1)[-1]


@pytest.fixture()
def anon(live_db: str) -> Any:
    """A client with no session at all."""
    from fastapi.testclient import TestClient

    from archguard.dashboard.app import app

    return TestClient(app)


@requires_postgres
def test_sharing_returns_a_usable_link(auth_client: Any, seed_run: Any) -> None:
    job_id = seed_run(repo_url=_URL, score=87.5)

    resp = _share(auth_client, job_id)
    assert resp.status_code == 200, resp.text
    url = resp.json()["share_url"]
    assert "/shared/" in url
    assert len(url.rsplit("/", 1)[-1]) >= 32


@requires_postgres
def test_an_anonymous_visitor_can_read_a_shared_report(
    auth_client: Any, seed_run: Any, anon: Any
) -> None:
    job_id = seed_run(repo_url=_URL, score=64.0)
    token = _token_from(_share(auth_client, job_id))

    page = anon.get(f"/shared/{token}")
    assert page.status_code == 200, page.text
    assert "Shared report" in page.text
    assert "64.0" in page.text


@requires_postgres
def test_the_shared_page_does_not_ship_the_dashboard_bundle(
    auth_client: Any, seed_run: Any, anon: Any
) -> None:
    """Server-rendered on purpose.

    Serving the dashboard's module graph to an anonymous visitor would give a
    shared link a running client that knows how to call the authenticated API,
    which is a much larger surface than "read this one report".
    """
    job_id = seed_run(repo_url=_URL)
    token = _token_from(_share(auth_client, job_id))

    body = anon.get(f"/shared/{token}").text
    assert "js/main.js" not in body
    assert "auth.js" not in body


@requires_postgres
def test_an_unknown_token_is_404_not_401(anon: Any) -> None:
    """401 would confirm that the URL shape is right and only the token wrong."""
    resp = anon.get("/shared/" + "z" * 43)
    assert resp.status_code == 404


@requires_postgres
def test_a_short_or_empty_token_never_resolves(anon: Any, auth_client: Any, seed_run: Any) -> None:
    seed_run(repo_url=_URL)
    for token in ("x", "short", "-" * 10):
        assert anon.get(f"/shared/{token}").status_code == 404


@requires_postgres
def test_a_revoked_link_stops_working_immediately(
    auth_client: Any, seed_run: Any, anon: Any
) -> None:
    job_id = seed_run(repo_url=_URL)
    token = _token_from(_share(auth_client, job_id))
    assert anon.get(f"/shared/{token}").status_code == 200

    revoked = auth_client.request("DELETE", f"/api/v1/runs/{job_id}/share")
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True

    assert anon.get(f"/shared/{token}").status_code == 404


@requires_postgres
def test_a_shared_page_is_not_cached(auth_client: Any, seed_run: Any, anon: Any) -> None:
    """Revocation is worthless if a proxy keeps serving the last copy."""
    job_id = seed_run(repo_url=_URL)
    token = _token_from(_share(auth_client, job_id))

    page = anon.get(f"/shared/{token}")
    assert "no-store" in page.headers.get("cache-control", "")
    assert "noindex" in page.headers.get("x-robots-tag", "")


@requires_postgres
def test_sharing_someone_elses_run_is_404(seed_run: Any, live_db: str, auth_client: Any) -> None:
    """Same answer as a run that does not exist, so ids cannot be probed."""
    job_id = seed_run(repo_url=_URL)

    from fastapi.testclient import TestClient

    from archguard.dashboard import _sessions
    from archguard.dashboard.app import app
    from archguard.db import store
    from archguard.db.session import session_scope
    from tests.db_fixtures import _run

    async def _make() -> int:
        async with session_scope() as session:
            user = await store.upsert_user(session, github_id=96001, login="share-outsider")
            return int(user.id)

    stranger = TestClient(app)
    stranger.cookies.set(_sessions.COOKIE_NAME, _sessions.issue(_run(_make())))

    assert stranger.post(f"/api/v1/runs/{job_id}/share").status_code == 404


@requires_postgres
def test_an_anonymous_caller_cannot_mint_a_link(anon: Any, seed_run: Any) -> None:
    """The public surface is read-only: holding a link must not confer sharing."""
    job_id = seed_run(repo_url=_URL)
    resp = anon.post(f"/api/v1/runs/{job_id}/share")
    # Either rejected outright, or -- on a dev instance where the local-account
    # fallback applies -- refused because the run belongs to somebody else.
    assert resp.status_code in (401, 403, 404), resp.text


@requires_postgres
def test_revoking_something_never_shared_is_success_that_changed_nothing(
    auth_client: Any, seed_run: Any
) -> None:
    job_id = seed_run(repo_url=_URL)
    resp = auth_client.request("DELETE", f"/api/v1/runs/{job_id}/share")
    assert resp.status_code == 200
    assert resp.json()["revoked"] is False


@requires_postgres
def test_sharing_twice_returns_the_same_link(auth_client: Any, seed_run: Any) -> None:
    job_id = seed_run(repo_url=_URL)
    first = _share(auth_client, job_id).json()["share_url"]
    second = _share(auth_client, job_id).json()["share_url"]
    assert first == second, "the second click invalidated a link already sent"
