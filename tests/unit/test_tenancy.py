"""User A must not be able to read user B's anything.

This is the test for D1, the one hard blocker to a public launch. The dashboard
had a single shared token, so any authenticated visitor could list every job id
the instance had ever issued and then read the repository URLs, module names,
file paths and violations behind each one.

The matrix is deliberately exhaustive rather than representative. Isolation is
not a property you can spot-check: it holds only if it holds on *every* route,
and the one that gets missed is the one that leaks. Each endpoint is asserted
twice -- the owner can read it, and the other user gets 404 -- because an
endpoint that returns 404 to everybody would pass a one-sided test while being
completely broken.

404, never 403: 403 confirms the id exists, which is precisely what an
enumeration attempt is trying to learn.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from archguard.dashboard import _sessions
from archguard.dashboard.app import app
from tests.db_fixtures import requires_postgres

pytestmark = requires_postgres

SESSION_SECRET = "0" * 64


@pytest.fixture
def two_users(live_db, monkeypatch):
    """Two accounts, each with a job and a stored run. Returns their cookies."""
    monkeypatch.setenv("SESSION_SECRET", SESSION_SECRET)
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_OAUTH_CLIENT_SECRET", raising=False)
    from archguard.dashboard._rate_limit import reset_rate_limits

    reset_rate_limits()
    _sessions.reset_sessions()

    from archguard.db import store
    from archguard.db.session import session_scope
    from tests.db_fixtures import _run

    async def _seed() -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, github_id, repo_url in (
            ("a", 1001, "https://github.com/alice/project"),
            ("b", 1002, "https://github.com/bob/project"),
        ):
            async with session_scope() as session:
                user = await store.upsert_user(
                    session, github_id=github_id, login=f"user-{name}"
                )
                job = await store.create_job(session, repo_url, user_id=user.id)
                job_id, user_id = job.id, user.id
                await store.set_job_status(session, job_id, "complete")
                await store.persist_run(
                    session,
                    job_id,
                    {
                        "repo_url": repo_url,
                        "score": 80.0,
                        "band": "PASS",
                        "module_scores": {f"{name}_module": 80.0},
                        "modules_analyzed": [f"{name}_module"],
                        "dependency_graph": {f"{name}_module": []},
                        "violations": [
                            {
                                "layer": 1,
                                "module": f"{name}_module",
                                "severity": "high",
                                "message": f"{name} secret finding",
                            }
                        ],
                    },
                )
            async with session_scope() as session:
                await store.save_dependency_scan(
                    session, job_id, user_id, {"score": 99.0, "scanned_packages": 7}
                )
            out[name] = {
                "user_id": user_id,
                "job_id": job_id,
                "repo_url": repo_url,
                "module": f"{name}_module",
            }
        return out

    seeded = _run(_seed())
    for name in ("a", "b"):
        seeded[name]["cookie"] = _sessions.issue(seeded[name]["user_id"])
    return seeded


def _client(cookie: str | None = None) -> TestClient:
    client = TestClient(app)
    if cookie:
        client.cookies.set(_sessions.COOKIE_NAME, cookie)
    return client


def _endpoints(who: dict[str, Any]) -> list[tuple[str, str]]:
    """(method, url) for every route that serves one user's data."""
    job = who["job_id"]
    return [
        ("GET", f"/api/v1/jobs/{job}"),
        ("GET", f"/api/v1/runs?job_id={job}"),
        ("GET", f"/api/v1/runs/latest?job_id={job}"),
        ("GET", f"/api/v1/modules?job_id={job}"),
        ("GET", f"/api/v1/trends/{who['module']}?job_id={job}"),
        ("GET", f"/api/v1/deps?job_id={job}"),
        ("GET", f"/api/v1/risk?job_id={job}"),
        ("GET", f"/api/v1/suppressions?job_id={job}"),
        ("GET", f"/api/v1/evolution/summary?job_id={job}"),
        ("GET", f"/api/v1/evolution/history?job_id={job}"),
        ("GET", f"/api/v1/evolution/trends?job_id={job}"),
        ("GET", f"/api/v1/repos/{who['repo_url']}/runs"),
    ]


@requires_postgres
def test_the_owner_can_read_their_own_data(two_users):
    """The other half of the matrix.

    Without this, an endpoint that 404s for everyone would pass the isolation
    assertions below while being entirely broken.
    """
    client = _client(two_users["a"]["cookie"])
    for method, url in _endpoints(two_users["a"]):
        response = client.request(method, url)
        assert response.status_code == 200, (
            f"{method} {url} -> {response.status_code}: the owner must be able "
            f"to read their own data. {response.text[:200]}"
        )


@requires_postgres
def test_another_user_gets_404_not_their_neighbours_data(two_users):
    """The D1 regression test.

    Scanned for A's data rather than only for a status code, because a 200
    carrying an empty payload is a correct answer and a 200 carrying A's rows
    is the bug -- and the two are indistinguishable by status alone.

    Two endpoints take the identifier in the path and echo it back --
    ``/trends/{module}`` and ``/repos/{url}/runs`` -- so for those the
    assertion is on the payload rather than the raw body. Echoing a value the
    caller supplied reveals nothing; the data array is what would.
    """
    client = _client(two_users["b"]["cookie"])
    echoed = {"/trends/": "trend", "/repos/": "runs"}
    for method, url in _endpoints(two_users["a"]):
        response = client.request(method, url)
        assert response.status_code in (200, 404), (
            f"{method} {url} -> unexpected {response.status_code}"
        )
        field = next((f for k, f in echoed.items() if k in url), None)
        if response.status_code == 200 and field:
            assert response.json()[field] == [], f"{url} leaked A's {field}"
            continue
        body = response.text
        assert "a secret finding" not in body, f"{method} {url} leaked A's violation"
        assert "a_module" not in body, f"{method} {url} leaked A's module names"
        assert "alice" not in body, f"{method} {url} leaked A's repository URL"


@requires_postgres
def test_job_list_shows_only_your_own_jobs(two_users):
    """The enumeration primitive. Unscoped, this returned every job id."""
    for name, other in (("a", "b"), ("b", "a")):
        response = _client(two_users[name]["cookie"]).get("/api/v1/jobs")
        assert response.status_code == 200
        ids = {j["job_id"] for j in response.json()["jobs"]}
        assert ids == {two_users[name]["job_id"]}
        assert two_users[other]["job_id"] not in ids


@requires_postgres
def test_recent_runs_without_a_job_id_are_still_scoped(two_users):
    """`/runs` with no job_id used to mean "the whole server's history"."""
    response = _client(two_users["b"]["cookie"]).get("/api/v1/runs")
    assert response.status_code == 200
    urls = {r["repo_url"] for r in response.json()["runs"]}
    assert urls == {two_users["b"]["repo_url"]}


@requires_postgres
def test_signed_out_requests_are_rejected(two_users):
    """No cookie, no data -- including from a non-loopback caller."""
    client = TestClient(app, client=("203.0.113.7", 5555))
    for method, url in _endpoints(two_users["a"]):
        response = client.request(method, url)
        assert response.status_code == 401, f"{method} {url} -> {response.status_code}"


@requires_postgres
def test_the_ops_token_is_not_accepted_as_a_user(two_users, monkeypatch):
    """ARCHGUARD_DASHBOARD_TOKEN identifies no one, so it reads nobody's data.

    It stays valid for operator endpoints. What it must never do is satisfy
    "whose rows?" -- answering that with "any of them" is the hole this task
    closes.
    """
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "ops-token-value")
    from archguard.dashboard._rate_limit import reset_rate_limits

    reset_rate_limits()

    client = TestClient(app, client=("203.0.113.7", 5555))
    job = two_users["a"]["job_id"]
    response = client.get(
        f"/api/v1/runs?job_id={job}", headers={"Authorization": "Bearer ops-token-value"}
    )
    assert response.status_code == 401, (
        "the operator credential must not resolve to a user"
    )


@requires_postgres
def test_the_ops_token_is_not_accepted_in_a_query_string(two_users, monkeypatch):
    """D2. A raw admin credential in a URL lands in proxy logs and history."""
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "ops-token-value")
    from archguard.dashboard._rate_limit import reset_rate_limits

    reset_rate_limits()

    client = TestClient(app, client=("203.0.113.7", 5555))
    response = client.get("/api/v1/runs?token=ops-token-value")
    assert response.status_code == 401


@requires_postgres
def test_a_forged_session_cookie_is_rejected(two_users):
    """The signature is checked before the store is touched."""
    session_id = two_users["a"]["cookie"].partition(".")[0]
    for forged in (
        f"{session_id}.deadbeef",
        f"{session_id}.",
        session_id,
        "..",
        "",
    ):
        assert _sessions.resolve(forged) is None, f"accepted forged cookie {forged!r}"


@requires_postgres
def test_a_session_resolves_to_exactly_one_user(two_users):
    for name in ("a", "b"):
        assert (
            _sessions.resolve(two_users[name]["cookie"]) == two_users[name]["user_id"]
        )


@requires_postgres
def test_revoking_a_session_ends_it(two_users):
    cookie = two_users["a"]["cookie"]
    assert _sessions.resolve(cookie) is not None
    _sessions.revoke(cookie)
    assert _sessions.resolve(cookie) is None


@requires_postgres
def test_writes_are_scoped_too(two_users):
    """Suppressions and dependency scans must not attach to a stranger's job."""
    from archguard.db import store
    from archguard.db.session import session_scope
    from tests.db_fixtures import _run

    a_job = two_users["a"]["job_id"]
    b_user = two_users["b"]["user_id"]

    async def _attempt() -> None:
        async with session_scope() as session:
            await store.save_dependency_scan(session, a_job, b_user, {"score": 0.0})

    with pytest.raises(ValueError):
        _run(_attempt())


@requires_postgres
def test_auth_status_is_answerable_while_signed_out(two_users):
    """The page asks this before it knows whether to show a sign-in button."""
    response = TestClient(app, client=("203.0.113.7", 5555)).get("/api/v1/auth/status")
    assert response.status_code == 200
    assert response.json()["authenticated"] is False


@requires_postgres
def test_auth_status_names_the_signed_in_user(two_users):
    response = _client(two_users["a"]["cookie"]).get("/api/v1/auth/status")
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["user"]["login"] == "user-a"


# ------------------------------------------------------- watched repositories
#
# Watching is the only feature where our infrastructure acts on a user's
# behalf unattended: it schedules scans and calls a URL they supplied. A watch
# that the wrong account can read or steer is a way to make our workers scan
# for someone else and post the result somewhere they chose. So the HTTP
# surface gets its own isolation assertions rather than relying on the store
# tests alone.


@requires_postgres
def test_a_watch_is_invisible_to_the_other_user(two_users):
    owner = _client(two_users["a"]["cookie"])
    created = owner.post(
        "/api/v1/watch", json={"repo_url": two_users["a"]["repo_url"]}
    )
    assert created.status_code == 201, created.text

    listed = _client(two_users["b"]["cookie"]).get("/api/v1/watch")
    assert listed.status_code == 200
    assert listed.json()["watched"] == []
    assert two_users["a"]["repo_url"] not in listed.text


@requires_postgres
def test_another_user_cannot_steer_or_delete_a_watch(two_users):
    """404 on both, and the watch is untouched afterwards."""
    owner = _client(two_users["a"]["cookie"])
    watch_id = owner.post(
        "/api/v1/watch",
        json={"repo_url": two_users["a"]["repo_url"], "health_drop_threshold": 3.0},
    ).json()["watched"]["id"]

    stranger = _client(two_users["b"]["cookie"])
    assert stranger.patch(
        f"/api/v1/watch/{watch_id}",
        json={"webhook_url": "https://attacker.example.com/steal"},
    ).status_code == 404
    assert stranger.delete(f"/api/v1/watch/{watch_id}").status_code == 404

    surviving = owner.get("/api/v1/watch").json()["watched"]
    assert len(surviving) == 1
    assert surviving[0]["health_drop_threshold"] == 3.0
    assert surviving[0]["has_webhook"] is False


@requires_postgres
def test_watching_requires_a_session(two_users):
    anonymous = TestClient(app, client=("203.0.113.9", 5555))
    assert anonymous.get("/api/v1/watch").status_code == 401
    assert anonymous.post(
        "/api/v1/watch", json={"repo_url": "https://github.com/x/y"}
    ).status_code == 401


@requires_postgres
def test_an_unsafe_webhook_url_is_refused(two_users):
    """The SSRF guard, at the point a user can first reach it.

    A watch pointed at an internal address turns our scheduled scans into a
    request generator inside our own network -- so this is checked when the URL
    is configured, and again in `send_generic_webhook` at every send, because
    DNS can be repointed in between.
    """
    client = _client(two_users["a"]["cookie"])
    for bad in (
        "http://example.com/hook",          # plaintext
        "https://127.0.0.1/hook",           # loopback
        "https://10.0.0.5/hook",            # private range
        "https://[::1]/hook",               # loopback, v6
    ):
        response = client.post(
            "/api/v1/watch",
            json={"repo_url": two_users["a"]["repo_url"], "webhook_url": bad},
        )
        assert response.status_code == 400, f"{bad} was accepted: {response.text[:200]}"
        assert "rejected" in response.text.lower()


@requires_postgres
def test_a_watch_never_returns_the_webhook_url(two_users, resolves_only):
    """It routinely carries a token in its path. The UI needs to know one is
    set, not what it is.

    The hostname is resolved by a stub. Accepting a webhook means passing the
    SSRF guard, and passing it means resolving the name -- so this test used to
    make a real DNS query for example.com and fail with a 400 whenever the
    resolver was slow or unreachable, for a reason that has nothing to do with
    whether a secret leaks into a response.
    """
    lookups = resolves_only("example.com", "93.184.216.34")

    client = _client(two_users["a"]["cookie"])
    created = client.post(
        "/api/v1/watch",
        json={
            "repo_url": two_users["a"]["repo_url"],
            "webhook_url": "https://example.com/t/SUPERSECRET",
        },
    )
    assert created.status_code == 201, created.text
    assert lookups == ["example.com"], (
        f"the hostname did not go through the stub resolver: {lookups}"
    )
    assert "SUPERSECRET" not in created.text
    assert created.json()["watched"]["has_webhook"] is True
    assert "SUPERSECRET" not in client.get("/api/v1/watch").text
