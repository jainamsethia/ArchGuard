"""Deleting an account has to delete the account.

The privacy policy says: "Ask, and your account and every analysis attached to
it are removed. Deleting an account deletes its jobs, runs and findings with
it." Until now there was no way to ask -- no endpoint, no store function, no
control, and no address on the page to write to. The promise named a mechanism
that did not exist.

Two things are easy to get wrong here and both are tested directly rather than
inferred from a 200.

The first is what "removed" means at the database. Every table that references
a user declares `ondelete="CASCADE"`, but `jobs.user_id` and `runs.user_id` are
nullable, and a SQLAlchemy relationship without `passive_deletes` will helpfully
null out the children before deleting the parent instead of letting the database
cascade. That succeeds, returns 200, and leaves every job, run and finding on
disk with no owner -- which is worse than not deleting at all, because the user
has been told it is gone. So these tests count rows afterwards.

The second is the session. An account that is gone must not still be usable by
whoever is holding a cookie for it -- including in another browser, which no
amount of clearing this browser's cookie reaches.

Against real PostgreSQL: what is being tested is a database cascade, and a fake
would be asserting that the fake cascades.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import func, select

from tests.db_fixtures import TEST_SESSION_SECRET, requires_postgres

pytestmark = pytest.mark.integration

DELETE_URL = "/api/v1/auth/account"


@pytest.fixture(autouse=True)
def _no_dev_login(monkeypatch):
    """Exercise the production authentication path.

    With no OAuth app configured and a loopback peer, every request is signed in
    as the local development account -- so a deleted account is immediately
    recreated and deletion looks broken when it is not. Configuring an OAuth app
    is what turns that fallback off; the credentials are never used, because
    nothing here starts an OAuth flow.
    """
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "not-a-real-client-id")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "not-a-real-client-secret")


def _run(coro):
    return asyncio.run(coro)


async def _make_user(github_id: int, login: str) -> int:
    from archguard.db import store
    from archguard.db.session import session_scope

    async with session_scope() as session:
        user = await store.upsert_user(session, github_id=github_id, login=login)
        return user.id


async def _give_them_data(user_id: int, url: str) -> str:
    """A job, a run with findings, a suppression and a watch.

    One of each user-owned table, so a cascade that misses one shows up as a
    surviving row rather than as nothing at all.
    """
    from archguard.db import store
    from archguard.db.session import session_scope

    async with session_scope() as session:
        job = await store.create_job(session, url, user_id=user_id)
        job_id = job.id

    async with session_scope() as session:
        await store.persist_run(
            session,
            job_id,
            {
                "repo_url": url,
                "score": 61.0,
                "band": "WARN",
                "violations": [
                    {
                        "file": "a/b.py",
                        "line": 3,
                        "module": "a",
                        "severity": "high",
                        "message": "fan_out=9 exceeds budget=3",
                        "layer": "2",
                        "scope": "module",
                        "kind": "fan_out",
                        "metrics": {},
                    }
                ],
            },
        )

    async with session_scope() as session:
        await store.add_suppression(
            session,
            user_id=user_id,
            repo_url=url,
            module="a",
            layer=2,
            violation_hash="a" * 64,
            reason="not now",
        )
        await store.watch_repository(session, user_id, url)

    return job_id


async def _counts(user_id: int) -> dict[str, int]:
    """What is left in the database for this user, table by table."""
    from archguard.db.models import (
        Job,
        Run,
        Suppression,
        User,
        WatchedRepository,
    )
    from archguard.db.session import session_scope

    async with session_scope() as session:
        out = {}
        for name, model in (
            ("users", User),
            ("jobs", Job),
            ("runs", Run),
            ("suppressions", Suppression),
            ("watches", WatchedRepository),
        ):
            column = model.id if model is User else model.user_id
            out[name] = (
                await session.execute(
                    select(func.count()).select_from(model).where(column == user_id)
                )
            ).scalar_one()
        return out


async def _orphan_counts() -> dict[str, int]:
    """Rows whose owner has been nulled out rather than deleted.

    The failure mode this file exists for: the ORM detaches the children, the
    request answers 200, and the data is still there belonging to nobody.
    """
    from archguard.db.models import Job, Run
    from archguard.db.session import session_scope

    async with session_scope() as session:
        return {
            "jobs": (
                await session.execute(
                    select(func.count()).select_from(Job).where(Job.user_id.is_(None))
                )
            ).scalar_one(),
            "runs": (
                await session.execute(
                    select(func.count()).select_from(Run).where(Run.user_id.is_(None))
                )
            ).scalar_one(),
        }


@pytest.fixture()
def account(live_db, monkeypatch) -> dict[str, Any]:
    """A signed-in client for an account that has something to lose."""
    from fastapi.testclient import TestClient

    from archguard.dashboard import _sessions
    from archguard.dashboard.app import app

    monkeypatch.setenv("SESSION_SECRET", TEST_SESSION_SECRET)

    user_id = _run(_make_user(8801, "deletes-self"))
    job_id = _run(_give_them_data(user_id, "https://github.com/x/deleted-account"))

    cookie = _sessions.issue(user_id)
    client = TestClient(app)
    client.cookies.set(_sessions.COOKIE_NAME, cookie)
    return {"client": client, "cookie": cookie, "user_id": user_id, "job_id": job_id}


# ------------------------------------------------------------- the happy path


@requires_postgres
def test_the_account_had_something_to_delete(account):
    """The baseline. Without it every assertion below could pass on an account
    that never had any data."""
    before = _run(_counts(account["user_id"]))
    assert before == {
        "users": 1,
        "jobs": 1,
        "runs": 1,
        "suppressions": 1,
        "watches": 1,
    }, before


@requires_postgres
def test_deleting_an_account_removes_the_user(account):
    assert account["client"].delete(DELETE_URL).status_code == 200
    assert _run(_counts(account["user_id"]))["users"] == 0


@requires_postgres
def test_everything_attached_to_the_account_goes_with_it(account):
    """The promise, stated as row counts.

    Not inferred from the response: a deletion that answers 200 and leaves the
    rows behind is exactly the failure this is for.
    """
    account["client"].delete(DELETE_URL)

    remaining = _run(_counts(account["user_id"]))
    assert remaining == {
        "users": 0,
        "jobs": 0,
        "runs": 0,
        "suppressions": 0,
        "watches": 0,
    }, f"deleting the account left rows behind: {remaining}"


@requires_postgres
def test_the_data_is_deleted_rather_than_orphaned(account):
    """The ORM trap, on its own.

    `jobs.user_id` and `runs.user_id` are nullable, so a relationship cascade
    that nulls them instead of deleting the rows satisfies the foreign key and
    passes every test that only checks the user is gone. The analysis would
    still be in the database, unowned and unreachable, after the user was told
    it had been removed.
    """
    before = _run(_orphan_counts())
    account["client"].delete(DELETE_URL)
    after = _run(_orphan_counts())

    assert after == before, (
        f"deletion orphaned rows instead of removing them: {before} -> {after}"
    )


@requires_postgres
def test_the_findings_go_too(account):
    """Violations hang off runs rather than off the user, so they are one
    cascade further out and would be the first thing a partial delete misses."""
    from archguard.db.models import Run, Violation
    from archguard.db.session import session_scope

    async def _violations() -> int:
        async with session_scope() as session:
            return (
                await session.execute(
                    select(func.count())
                    .select_from(Violation)
                    .join(Run, Violation.run_id == Run.id)
                    .where(Run.user_id == account["user_id"])
                )
            ).scalar_one()

    assert _run(_violations()) == 1, "the fixture stored no findings to delete"
    account["client"].delete(DELETE_URL)
    assert _run(_violations()) == 0


# ------------------------------------------------------------------- sessions


@requires_postgres
def test_the_session_stops_working(account):
    assert account["client"].get("/api/v1/auth/status").json()["authenticated"] is True

    account["client"].delete(DELETE_URL)

    assert account["client"].get("/api/v1/auth/status").json()["authenticated"] is False


@requires_postgres
def test_a_cookie_held_somewhere_else_stops_working_too(account):
    """The half that clearing this browser's cookie cannot reach.

    Someone who deletes their account on a shared machine has to be sure it is
    gone everywhere, not just here.
    """
    from fastapi.testclient import TestClient

    from archguard.dashboard import _sessions
    from archguard.dashboard.app import app

    elsewhere = TestClient(app)
    elsewhere.cookies.set(_sessions.COOKIE_NAME, account["cookie"])
    assert elsewhere.get("/api/v1/auth/status").json()["authenticated"] is True

    account["client"].delete(DELETE_URL)

    assert elsewhere.get("/api/v1/auth/status").json()["authenticated"] is False, (
        "a session for the deleted account was still accepted"
    )
    assert elsewhere.get("/api/v1/jobs").status_code == 401


@requires_postgres
def test_the_browser_is_told_to_drop_the_cookie(account):
    from archguard.dashboard import _sessions

    header = account["client"].delete(DELETE_URL).headers.get("set-cookie", "")

    assert _sessions.COOKIE_NAME in header, f"no cookie instruction: {header!r}"
    assert "max-age=0" in header.lower(), f"the cookie was not expired: {header!r}"


# ------------------------------------------------------------------- tenancy


@requires_postgres
def test_deleting_one_account_leaves_everyone_else_alone(account, live_db):
    """A cascade keyed on the wrong column would take the whole table."""
    other_id = _run(_make_user(8802, "stays-put"))
    _run(_give_them_data(other_id, "https://github.com/x/still-here"))

    account["client"].delete(DELETE_URL)

    survived = _run(_counts(other_id))
    assert survived == {
        "users": 1,
        "jobs": 1,
        "runs": 1,
        "suppressions": 1,
        "watches": 1,
    }, f"another account lost data: {survived}"


# --------------------------------------------------------------- refusals


@requires_postgres
def test_a_signed_out_caller_cannot_delete_anything(live_db, monkeypatch):
    from fastapi.testclient import TestClient

    from archguard.dashboard.app import app

    monkeypatch.setenv("SESSION_SECRET", TEST_SESSION_SECRET)
    assert TestClient(app).delete(DELETE_URL).status_code == 401


@requires_postgres
def test_deleting_twice_is_not_an_error(account):
    """The second request arrives with a cookie for an account that is gone, so
    it is simply not signed in -- a refusal, not a crash."""
    assert account["client"].delete(DELETE_URL).status_code == 200
    assert account["client"].delete(DELETE_URL).status_code == 401


def test_the_privacy_page_describes_something_that_exists():
    """The defect this task began as.

    The page said "Ask, and your account and every analysis attached to it are
    removed" while naming no way to ask -- no control, no endpoint, and no
    address to write to. A promise about someone's data is worth checking
    against the code that is supposed to keep it, so this reads the page and
    requires the mechanism it describes to be mounted.
    """
    from pathlib import Path

    from archguard.dashboard.app import app

    page = (
        Path(__file__).resolve().parents[2]
        / "archguard"
        / "dashboard"
        / "templates"
        / "privacy.html"
    ).read_text(encoding="utf-8")

    assert "Delete account" in page, (
        "the privacy page no longer names the control that performs deletion"
    )
    assert DELETE_URL in {getattr(r, "path", "") for r in app.routes}, (
        "the privacy page promises deletion and the endpoint is not mounted"
    )
    assert "Ask, and your account" not in page, (
        "the page still tells people to ask, with nowhere to ask"
    )


def test_deletion_is_not_reachable_by_following_a_link():
    """A GET would be followed by prefetchers, link scanners and the browser's
    own speculative loading. Nothing that destroys an account may be one
    navigation away."""
    from archguard.dashboard.app import app

    routes = [
        r
        for r in app.routes
        if getattr(r, "path", "") == DELETE_URL
    ]
    assert routes, f"{DELETE_URL} is not mounted"
    for route in routes:
        methods = set(getattr(route, "methods", set()))
        assert "GET" not in methods, f"account deletion answers GET: {methods}"
        assert "DELETE" in methods, f"expected DELETE, got {methods}"
