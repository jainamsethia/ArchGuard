"""Watched repositories, against a real database.

Two things here are load-bearing enough to be worth the Postgres round trip.

The first is tenancy. A watched repository is the one object in the product
that our infrastructure acts on unattended -- it schedules work, and it calls a
URL somebody supplied. If a watch could be read or steered by anyone but its
owner, that becomes a way to make our workers scan on someone else's behalf and
post the result to an address they chose. So every cross-user path is tested
explicitly rather than trusted to the `where user_id ==` being present.

The second is the duplicate guard. Retries are the normal case, not the
exception: a worker that dies after sending an alert and before recording it
will be retried, and if the identity of the regression is not durable it sends
the alert again. Nothing catches that in a unit test, because the thing being
tested is precisely that the state survived the process.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from tests.db_fixtures import requires_postgres

pytestmark = pytest.mark.integration


def _run(coro):
    return asyncio.run(coro)


async def _two_users(store, session_scope):
    async with session_scope() as session:
        alice = await store.upsert_user(session, github_id=7101, login="w-alice")
        bob = await store.upsert_user(session, github_id=7102, login="w-bob")
        return alice.id, bob.id


# ------------------------------------------------------------------- lifecycle


@requires_postgres
def test_watching_a_repository_then_listing_it_back(live_db):
    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        alice, _ = await _two_users(store, session_scope)
        async with session_scope() as session:
            await store.watch_repository(
                session, alice, "https://github.com/x/watched-one"
            )
        async with session_scope() as session:
            return await store.list_watched(session, alice)

    watched = _run(scenario())
    assert len(watched) == 1
    assert watched[0]["repo_url"] == "https://github.com/x/watched-one"
    assert watched[0]["active"] is True
    assert watched[0]["schedule"] == "daily"


@requires_postgres
def test_watching_the_same_repository_twice_does_not_duplicate_it(live_db):
    """Otherwise the second submit doubles the scans and the alerts."""
    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        alice, _ = await _two_users(store, session_scope)
        url = "https://github.com/x/watched-twice"
        async with session_scope() as session:
            await store.watch_repository(session, alice, url, health_drop_threshold=5.0)
        async with session_scope() as session:
            await store.watch_repository(session, alice, url, health_drop_threshold=9.0)
        async with session_scope() as session:
            return await store.list_watched(session, alice)

    watched = _run(scenario())
    assert len(watched) == 1
    assert watched[0]["health_drop_threshold"] == 9.0, "the second submit did not update"


@requires_postgres
def test_an_inactive_watch_is_never_due(live_db):
    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        alice, _ = await _two_users(store, session_scope)
        async with session_scope() as session:
            watch = await store.watch_repository(
                session, alice, "https://github.com/x/watched-inactive"
            )
            watch_id = watch.id
            await store.update_watched(session, watch_id, alice, active=False)
        async with session_scope() as session:
            due = await store.watches_due(session, datetime.now(UTC))
            return watch_id, [w["id"] for w in due]

    watch_id, due_ids = _run(scenario())
    assert watch_id not in due_ids


@requires_postgres
def test_a_watch_checked_recently_is_not_due_again(live_db):
    """The scheduler must not rescan the same repository every tick."""
    from archguard.db import store
    from archguard.db.models import WatchedRepository
    from archguard.db.session import session_scope

    async def scenario():
        alice, _ = await _two_users(store, session_scope)
        async with session_scope() as session:
            watch = await store.watch_repository(
                session, alice, "https://github.com/x/watched-recent"
            )
            watch_id = watch.id
        async with session_scope() as session:
            row = await session.get(WatchedRepository, watch_id)
            row.last_checked_at = datetime.now(UTC)
        async with session_scope() as session:
            due = await store.watches_due(session, datetime.now(UTC) - timedelta(hours=23))
            return watch_id, [w["id"] for w in due]

    watch_id, due_ids = _run(scenario())
    assert watch_id not in due_ids


@requires_postgres
def test_a_watch_checked_long_ago_is_due(live_db):
    from archguard.db import store
    from archguard.db.models import WatchedRepository
    from archguard.db.session import session_scope

    async def scenario():
        alice, _ = await _two_users(store, session_scope)
        async with session_scope() as session:
            watch = await store.watch_repository(
                session, alice, "https://github.com/x/watched-stale"
            )
            watch_id = watch.id
        async with session_scope() as session:
            row = await session.get(WatchedRepository, watch_id)
            row.last_checked_at = datetime.now(UTC) - timedelta(days=3)
        async with session_scope() as session:
            due = await store.watches_due(session, datetime.now(UTC) - timedelta(hours=23))
            return watch_id, [w["id"] for w in due]

    watch_id, due_ids = _run(scenario())
    assert watch_id in due_ids


# --------------------------------------------------------------------- tenancy


@requires_postgres
def test_one_user_never_sees_anothers_watched_repository(live_db):
    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        alice, bob = await _two_users(store, session_scope)
        async with session_scope() as session:
            await store.watch_repository(session, alice, "https://github.com/x/alice-secret")
        async with session_scope() as session:
            return await store.list_watched(session, bob)

    assert _run(scenario()) == []


@requires_postgres
def test_one_user_cannot_read_anothers_watch_by_id(live_db):
    """The id is a small integer. Guessing one must get you nothing."""
    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        alice, bob = await _two_users(store, session_scope)
        async with session_scope() as session:
            watch = await store.watch_repository(
                session, alice, "https://github.com/x/alice-by-id"
            )
            watch_id = watch.id
        async with session_scope() as session:
            return (
                await store.get_watched(session, watch_id, bob),
                await store.get_watched_summary(session, watch_id, bob),
                await store.get_watched(session, watch_id, alice),
            )

    as_bob, summary_as_bob, as_alice = _run(scenario())
    assert as_bob is None
    assert summary_as_bob is None
    assert as_alice is not None, "the owner lost access along with the stranger"


@requires_postgres
def test_one_user_cannot_modify_anothers_watch(live_db):
    """Including the webhook URL -- otherwise a stranger redirects the alerts."""
    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        alice, bob = await _two_users(store, session_scope)
        async with session_scope() as session:
            watch = await store.watch_repository(
                session,
                alice,
                "https://github.com/x/alice-modify",
                webhook_url="https://hooks.example.com/alice",
            )
            watch_id = watch.id
        async with session_scope() as session:
            hijacked = await store.update_watched(
                session, watch_id, bob, webhook_url="https://attacker.example.com/steal"
            )
        async with session_scope() as session:
            row = await store.get_watched(session, watch_id, alice)
            return hijacked, row.webhook_url

    hijacked, webhook = _run(scenario())
    assert hijacked is None
    assert webhook == "https://hooks.example.com/alice"


@requires_postgres
def test_one_user_cannot_delete_anothers_watch(live_db):
    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        alice, bob = await _two_users(store, session_scope)
        async with session_scope() as session:
            watch = await store.watch_repository(
                session, alice, "https://github.com/x/alice-delete"
            )
            watch_id = watch.id
        async with session_scope() as session:
            deleted = await store.delete_watched(session, watch_id, bob)
        async with session_scope() as session:
            return deleted, await store.get_watched(session, watch_id, alice)

    deleted, survivor = _run(scenario())
    assert deleted is False
    assert survivor is not None


@requires_postgres
def test_two_users_watch_the_same_repository_independently(live_db):
    """Public repositories are shared; watches are not. Each account keeps its
    own threshold, its own webhook and its own alert history."""
    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        alice, bob = await _two_users(store, session_scope)
        url = "https://github.com/x/popular"
        async with session_scope() as session:
            await store.watch_repository(session, alice, url, health_drop_threshold=2.0)
            await store.watch_repository(session, bob, url, health_drop_threshold=20.0)
        async with session_scope() as session:
            return (
                await store.list_watched(session, alice),
                await store.list_watched(session, bob),
            )

    a, b = _run(scenario())
    assert len(a) == 1 and len(b) == 1
    assert a[0]["health_drop_threshold"] == 2.0
    assert b[0]["health_drop_threshold"] == 20.0
    assert a[0]["id"] != b[0]["id"]


@requires_postgres
def test_a_run_by_one_user_never_matches_anothers_watch(live_db):
    """The alerting side of tenancy.

    Bob analyses a repository Alice watches. Alice must not be alerted about
    Bob's run: it is not her scan, and its findings are not hers to receive.
    """
    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        alice, bob = await _two_users(store, session_scope)
        url = "https://github.com/x/crossed"
        async with session_scope() as session:
            await store.watch_repository(session, alice, url)
            repo = await store.upsert_repository(session, url)
            repository_id = repo.id
        async with session_scope() as session:
            return await store.find_watch_for_run(session, bob, repository_id)

    assert _run(scenario()) is None


@requires_postgres
def test_the_webhook_url_is_never_returned_by_the_api(live_db):
    """A webhook URL can carry a token in its path. The UI needs to know one is
    set, not what it is."""
    from archguard.db import store
    from archguard.db.session import session_scope

    async def scenario():
        alice, _ = await _two_users(store, session_scope)
        async with session_scope() as session:
            await store.watch_repository(
                session,
                alice,
                "https://github.com/x/secret-hook",
                webhook_url="https://hooks.example.com/t/SECRETTOKEN",
            )
        async with session_scope() as session:
            return await store.list_watched(session, alice)

    payload = _run(scenario())
    assert payload[0]["has_webhook"] is True
    assert "SECRETTOKEN" not in str(payload)


# ------------------------------------------------------- regression evaluation


async def _watched_job(store, session_scope, url, user_id, score, previous_score=None):
    """A watched repository with an optional previous run, then a current one."""
    async with session_scope() as session:
        await store.watch_repository(session, user_id, url)

    if previous_score is not None:
        async with session_scope() as session:
            first = await store.create_job(session, url, user_id=user_id)
            first_id = first.id
        async with session_scope() as session:
            await store.persist_run(
                session, first_id, {"repo_url": url, "score": previous_score}
            )

    async with session_scope() as session:
        job = await store.create_job(session, url, user_id=user_id)
        job_id = job.id
    async with session_scope() as session:
        await store.persist_run(session, job_id, {"repo_url": url, "score": score})
    return job_id


@requires_postgres
def test_a_degrading_repository_records_a_regression(live_db):
    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.watch.service import evaluate_after_run

    async def scenario():
        alice, _ = await _two_users(store, session_scope)
        url = "https://github.com/x/falling"
        job_id = await _watched_job(
            store, session_scope, url, alice, score=60.0, previous_score=90.0
        )
        await evaluate_after_run(job_id)
        async with session_scope() as session:
            return (await store.list_watched(session, alice))[0]

    watch = _run(scenario())
    assert watch["last_checked_at"] is not None
    assert "fell" in (watch["last_status"] or "").lower()


@requires_postgres
def test_an_improving_repository_records_no_regression(live_db):
    """C10 from the other side: nobody wants to be told their code got better."""
    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.watch.service import evaluate_after_run

    async def scenario():
        alice, _ = await _two_users(store, session_scope)
        url = "https://github.com/x/rising"
        job_id = await _watched_job(
            store, session_scope, url, alice, score=95.0, previous_score=50.0
        )
        await evaluate_after_run(job_id)
        async with session_scope() as session:
            return (await store.list_watched(session, alice))[0]

    watch = _run(scenario())
    assert watch["last_alert_at"] is None
    assert "no regression" in (watch["last_status"] or "").lower()


@requires_postgres
def test_evaluating_an_unwatched_repository_does_nothing(live_db):
    """Every ordinary job calls this. It must be a cheap no-op for the vast
    majority that nobody is watching."""
    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.watch.service import evaluate_after_run

    async def scenario():
        alice, _ = await _two_users(store, session_scope)
        url = "https://github.com/x/unwatched"
        async with session_scope() as session:
            job = await store.create_job(session, url, user_id=alice)
            job_id = job.id
        async with session_scope() as session:
            await store.persist_run(session, job_id, {"repo_url": url, "score": 10.0})
        await evaluate_after_run(job_id)
        async with session_scope() as session:
            return await store.list_watched(session, alice)

    assert _run(scenario()) == []


@requires_postgres
def test_a_retry_does_not_alert_twice_for_the_same_regression(live_db):
    """The duplicate guard, tested the way it actually fails.

    `evaluate_after_run` is called a second time for the same job -- which is
    what a worker retry does. The first call records the alert key; the second
    must recognise it and not deliver again. Nothing in memory carries between
    them, which is the point: an in-memory flag would not survive the restart
    that causes the retry.
    """
    from archguard.db import store
    from archguard.db.models import WatchedRepository
    from archguard.db.session import session_scope
    from archguard.watch.service import evaluate_after_run

    sent: list[str] = []

    async def fake_send(url, alerts):
        sent.append(url)

    async def scenario():
        alice, _ = await _two_users(store, session_scope)
        url = "https://github.com/x/retried"
        async with session_scope() as session:
            await store.watch_repository(
                session, alice, url, webhook_url="https://hooks.example.com/ok"
            )

        async with session_scope() as session:
            first = await store.create_job(session, url, user_id=alice)
            first_id = first.id
        async with session_scope() as session:
            await store.persist_run(session, first_id, {"repo_url": url, "score": 90.0})
        async with session_scope() as session:
            job = await store.create_job(session, url, user_id=alice)
            job_id = job.id
        async with session_scope() as session:
            await store.persist_run(session, job_id, {"repo_url": url, "score": 60.0})

        from archguard.alerting import webhooks

        original = webhooks.send_generic_webhook
        webhooks.send_generic_webhook = fake_send
        try:
            await evaluate_after_run(job_id)
            await evaluate_after_run(job_id)  # the retry
        finally:
            webhooks.send_generic_webhook = original

        async with session_scope() as session:
            watch = (await store.list_watched(session, alice))[0]
            row = await session.get(WatchedRepository, watch["id"])
            return row.last_alert_key

    key = _run(scenario())
    assert len(sent) == 1, f"the regression was alerted {len(sent)} times, not once"
    assert key, "no durable alert key was recorded, so the next retry would resend"


@requires_postgres
def test_a_failed_delivery_leaves_the_alert_eligible_for_retry(live_db):
    """The opposite failure. Recording the key before the send succeeded would
    suppress the retry that should follow -- a missed regression, which is the
    thing this feature exists to prevent."""
    from archguard.db import store
    from archguard.db.models import WatchedRepository
    from archguard.db.session import session_scope
    from archguard.watch.service import evaluate_after_run

    async def exploding_send(url, alerts):
        raise RuntimeError("webhook endpoint is down")

    async def scenario():
        alice, _ = await _two_users(store, session_scope)
        url = "https://github.com/x/undeliverable"
        async with session_scope() as session:
            await store.watch_repository(
                session, alice, url, webhook_url="https://hooks.example.com/down"
            )
        async with session_scope() as session:
            first = await store.create_job(session, url, user_id=alice)
            first_id = first.id
        async with session_scope() as session:
            await store.persist_run(session, first_id, {"repo_url": url, "score": 90.0})
        async with session_scope() as session:
            job = await store.create_job(session, url, user_id=alice)
            job_id = job.id
        async with session_scope() as session:
            await store.persist_run(session, job_id, {"repo_url": url, "score": 60.0})

        from archguard.alerting import webhooks

        original = webhooks.send_generic_webhook
        webhooks.send_generic_webhook = exploding_send
        try:
            await evaluate_after_run(job_id)
        finally:
            webhooks.send_generic_webhook = original

        async with session_scope() as session:
            watch = (await store.list_watched(session, alice))[0]
            row = await session.get(WatchedRepository, watch["id"])
            return row.last_alert_key, row.last_checked_at

    key, checked = _run(scenario())
    assert key is None, "an undelivered alert was recorded as sent"
    assert checked is not None, "the scan was not recorded as checked"


@requires_postgres
def test_watch_state_survives_a_new_session(live_db):
    """Durable, not in-process. Everything above already reads through fresh
    sessions; this asserts it directly so the guarantee is named."""
    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.watch.service import evaluate_after_run

    async def scenario():
        alice, _ = await _two_users(store, session_scope)
        url = "https://github.com/x/durable"
        job_id = await _watched_job(
            store, session_scope, url, alice, score=60.0, previous_score=90.0
        )
        await evaluate_after_run(job_id)
        from archguard.db.session import dispose_engine

        await dispose_engine()
        async with session_scope() as session:
            return (await store.list_watched(session, alice))[0]

    watch = _run(scenario())
    assert watch["last_checked_at"] is not None
    assert watch["last_status"]


# ------------------------------------------------------------------- scheduling


@requires_postgres
def test_the_sweep_enqueues_a_job_for_a_due_watch(live_db):
    """The scheduled path goes through the ordinary analysis job.

    Asserted by what the sweep enqueues: a real job row owned by the watcher,
    handed to `enqueue_analysis` -- the same function the submit form calls.
    There is no separate 'watched repository analysis'.
    """
    from archguard.db import store
    from archguard.db.models import WatchedRepository
    from archguard.db.session import session_scope
    from archguard.worker import cron

    enqueued: list[str] = []

    async def fake_enqueue(job_id):
        enqueued.append(job_id)
        return "queued"

    async def scenario():
        alice, _ = await _two_users(store, session_scope)
        url = "https://github.com/x/due-for-sweep"
        async with session_scope() as session:
            watch = await store.watch_repository(session, alice, url)
            watch_id = watch.id
        async with session_scope() as session:
            row = await session.get(WatchedRepository, watch_id)
            row.last_checked_at = datetime.now(UTC) - timedelta(days=2)

        from archguard.worker import queue

        original = queue.enqueue_analysis
        queue.enqueue_analysis = fake_enqueue
        try:
            await cron.sweep_watched(None)
        finally:
            queue.enqueue_analysis = original

        async with session_scope() as session:
            # Read back through the user-scoped accessor: a job the watcher
            # cannot see is not a job that will produce their run.
            return [
                await store.get_job_repo_url(session, job_id, alice)
                for job_id in enqueued
            ]

    urls = _run(scenario())
    assert enqueued, "the sweep enqueued nothing for a due watch"
    assert "https://github.com/x/due-for-sweep" in urls, (
        "no enqueued job belonged to the watcher for the watched repository"
    )


@requires_postgres
def test_a_scheduled_job_belongs_to_the_watcher(live_db):
    """Not to nobody, and not to whoever else watches the same repository.

    The run it produces has to land in the watcher's history and be comparable
    against the watcher's previous run.
    """
    from archguard.db import store
    from archguard.db.models import WatchedRepository
    from archguard.db.session import session_scope
    from archguard.worker import cron

    enqueued: list[str] = []

    async def fake_enqueue(job_id):
        enqueued.append(job_id)
        return "queued"

    async def scenario():
        alice, bob = await _two_users(store, session_scope)
        url = "https://github.com/x/owned-sweep"
        async with session_scope() as session:
            watch = await store.watch_repository(session, alice, url)
            watch_id = watch.id
        async with session_scope() as session:
            row = await session.get(WatchedRepository, watch_id)
            row.last_checked_at = datetime.now(UTC) - timedelta(days=2)

        from archguard.worker import queue

        original = queue.enqueue_analysis
        queue.enqueue_analysis = fake_enqueue
        try:
            await cron.sweep_watched(None)
        finally:
            queue.enqueue_analysis = original

        async with session_scope() as session:
            owned_by_bob = []
            for job_id in enqueued:
                if await store.get_job(session, job_id, bob) is not None:
                    owned_by_bob.append(job_id)
            return owned_by_bob

    assert _run(scenario()) == [], "a scheduled job was readable by the wrong account"
