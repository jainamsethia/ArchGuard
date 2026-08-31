"""Where the dependency scan runs, and what the web process needs to serve it.

"Scan Dependencies" has never worked in production. C2 in the original audit
was that `pip-audit` lived in the dev dependency group and so was absent from
every deployed image; the worker split installed it -- into the worker image,
while `analyze_dependencies` was still being called from the web request
handler. The fix went into the container that does not run the code.

So the endpoint answered "pip-audit not found in PATH" before the split and
after it, and the CI check added alongside proves only that the package is
present somewhere, which was never the question.

Two reasons to move the scan rather than install pip-audit into the web image.
The obvious one is that the web image exists to be small: it carries no torch
and no model and should not grow a vulnerability scanner either. The one that
matters more is that scanning means running a subprocess over a requirements
file from a repository nobody vetted, and the process holding every session
key is the wrong place for that. The worker was split out for exactly this.

There is also a lifecycle reason the on-demand design could not have worked
well: the scan needs the clone, and the clone is deleted when the job ends. The
endpoint had a 410 for it. Scanning during the job removes that failure mode
rather than reporting it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests.db_fixtures import requires_postgres

pytestmark = pytest.mark.integration


#: A requirements file naming one real, ancient, known-vulnerable release. What
#: matters is that pip-audit parses it and returns findings; the specific
#: advisories are OSV's business and change over time, so nothing here asserts
#: on them.
VULNERABLE_REQUIREMENTS = "urllib3==1.24.1\n"


@pytest.fixture()
def repo_with_requirements(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text(VULNERABLE_REQUIREMENTS, encoding="utf-8")
    (repo / "app.py").write_text("import urllib3\n", encoding="utf-8")
    return repo


@pytest.fixture()
def owned_job(live_db):
    """A real job owned by a real account, which is what persistence requires."""
    from archguard.db import store
    from archguard.db.session import session_scope

    async def _go() -> dict:
        async with session_scope() as session:
            user = await store.upsert_user(session, github_id=9401, login="deps-owner")
            job = await store.create_job(
                session, "https://github.com/x/deps", user_id=user.id
            )
            return {"job_id": job.id, "user_id": user.id}

    return asyncio.run(_go())


def _stored(job_id: str, user_id: int) -> dict | None:
    from archguard.db import store
    from archguard.db.session import session_scope

    async def _go() -> dict | None:
        async with session_scope() as session:
            return await store.get_dependency_scan(session, job_id, user_id)

    return asyncio.run(_go())


# --------------------------------------------------- the scan runs in the worker


#: pip-audit's own output shape, so the parser and everything downstream of it
#: run for real. What is stood in for is the network round trip to the OSV
#: advisory database -- an external service, not part of this architecture.
#: `test_a_real_scan_end_to_end` below does the whole thing against the real
#: advisory data, and is marked slow because it takes minutes and needs egress.
PIP_AUDIT_JSON = {
    "dependencies": [
        {
            "name": "urllib3",
            "version": "1.24.1",
            "vulns": [
                {"id": "GHSA-wqvq-5m8c-6g24", "description": "CRLF injection."},
                {"id": "GHSA-www2-v7xj-xrcj", "description": "Redirect leak."},
            ],
        },
        {"name": "certifi", "version": "2020.4.5.1", "vulns": []},
    ]
}


def _fake_pip_audit(monkeypatch, payload=None):
    """Answer as pip-audit would, without asking OSV."""
    import json
    import subprocess
    from unittest.mock import MagicMock

    process = MagicMock()
    process.stdout = json.dumps(payload if payload is not None else PIP_AUDIT_JSON)
    process.stderr = ""
    process.returncode = 1  # pip-audit's normal exit when it finds something
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: process)


@requires_postgres
def test_the_worker_scans_and_persists(
    owned_job, repo_with_requirements, live_db, monkeypatch
):
    """The whole point: the result exists before anyone asks for it."""
    from archguard.worker.tasks import scan_dependencies

    _fake_pip_audit(monkeypatch)
    assert _stored(owned_job["job_id"], owned_job["user_id"]) is None

    asyncio.run(scan_dependencies(owned_job["job_id"], repo_with_requirements))

    stored = _stored(owned_job["job_id"], owned_job["user_id"])
    assert stored is not None, "the worker did not persist a dependency scan"
    assert stored["skipped"] is False, f"the scan was skipped: {stored['skip_reason']}"
    assert stored["scanned_packages"] == 2
    assert {v["package"] for v in stored["vulnerable_packages"]} == {"urllib3"}
    assert stored["score"] == 80.0, "two findings should cost twenty points"


@pytest.mark.slow
@requires_postgres
def test_a_real_scan_end_to_end(owned_job, repo_with_requirements, live_db):
    """The genuine article: real pip-audit, real advisory data, real database.

    Marked slow rather than mocked away. It needs egress to OSV and takes
    minutes, so it is deselected by default -- but it is the only thing that
    proves the subprocess this architecture depends on actually runs, so it
    stays runnable rather than being replaced by the stub above.
    """
    from archguard.worker.tasks import scan_dependencies

    asyncio.run(scan_dependencies(owned_job["job_id"], repo_with_requirements))

    stored = _stored(owned_job["job_id"], owned_job["user_id"])
    assert stored is not None
    if stored["skipped"]:
        pytest.skip(f"pip-audit unavailable here: {stored['skip_reason']}")
    assert stored["scanned_packages"] >= 1


@requires_postgres
def test_a_repository_with_no_requirements_records_why(owned_job, tmp_path, live_db):
    """Skipped is a result, not an absence.

    A job with nothing stored is indistinguishable from one whose scan never
    ran, so "there was nothing to scan" has to be written down.
    """
    from archguard.worker.tasks import scan_dependencies

    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "app.py").write_text("x = 1\n", encoding="utf-8")

    asyncio.run(scan_dependencies(owned_job["job_id"], bare))

    stored = _stored(owned_job["job_id"], owned_job["user_id"])
    assert stored is not None
    assert stored["skipped"] is True
    assert "requirements" in stored["skip_reason"].lower()


# ------------------------------------------------- failure must not be fatal


@requires_postgres
def test_a_missing_pip_audit_is_recorded_not_raised(owned_job, repo_with_requirements, live_db, monkeypatch):
    """The exact production symptom, now a recorded outcome.

    The web image has no pip-audit and never will. If the worker somehow lacks
    it too, the scan has to say so rather than take the analysis down with it.
    """
    import subprocess

    from archguard.worker.tasks import scan_dependencies

    def _absent(*args, **kwargs):
        raise FileNotFoundError("pip-audit")

    monkeypatch.setattr(subprocess, "run", _absent)
    asyncio.run(scan_dependencies(owned_job["job_id"], repo_with_requirements))

    stored = _stored(owned_job["job_id"], owned_job["user_id"])
    assert stored is not None, "a missing scanner left nothing recorded at all"
    assert stored["skipped"] is True
    assert "pip-audit" in stored["skip_reason"]


@requires_postgres
def test_a_timeout_is_recorded_not_raised(owned_job, repo_with_requirements, live_db, monkeypatch):
    import subprocess

    from archguard.worker.tasks import scan_dependencies

    def _slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="pip-audit", timeout=60)

    monkeypatch.setattr(subprocess, "run", _slow)
    asyncio.run(scan_dependencies(owned_job["job_id"], repo_with_requirements))

    stored = _stored(owned_job["job_id"], owned_job["user_id"])
    assert stored is not None
    assert stored["skipped"] is True
    assert "timed out" in stored["skip_reason"].lower()


@requires_postgres
def test_the_scan_never_raises_into_the_analysis(owned_job, repo_with_requirements, live_db, monkeypatch):
    """An analysis that succeeded must not be reported as failed because a
    vulnerability scan could not run. The scan is information about the
    repository, not part of measuring it."""
    from archguard.analysis import deps
    from archguard.worker.tasks import scan_dependencies

    def _explode(*args, **kwargs):
        raise RuntimeError("something entirely unexpected")

    monkeypatch.setattr(deps, "analyze_dependencies", _explode)

    # No exception, and the job is left in whatever state its analysis put it.
    asyncio.run(scan_dependencies(owned_job["job_id"], repo_with_requirements))


@requires_postgres
def test_a_job_that_does_not_exist_is_survivable(repo_with_requirements, live_db):
    """Persistence refuses to attach a scan to an unknown job. That refusal
    must not become an exception escaping into the worker loop."""
    from archguard.worker.tasks import scan_dependencies

    asyncio.run(
        scan_dependencies("00000000-0000-0000-0000-000000000000", repo_with_requirements)
    )


# ---------------------------------------------------------------- the web side


@requires_postgres
def test_the_endpoint_serves_the_stored_scan_without_scanning(
    owned_job, repo_with_requirements, live_db, monkeypatch
):
    """The web process must not need pip-audit.

    Enforced by making any attempt to scan from the request handler an error:
    if the route still reaches for the scanner, this fails.
    """
    from fastapi.testclient import TestClient

    from archguard.analysis import deps
    from archguard.dashboard import _sessions
    from archguard.dashboard.app import app
    from archguard.worker.tasks import scan_dependencies
    from tests.db_fixtures import TEST_SESSION_SECRET

    monkeypatch.setenv("SESSION_SECRET", TEST_SESSION_SECRET)
    _fake_pip_audit(monkeypatch)
    asyncio.run(scan_dependencies(owned_job["job_id"], repo_with_requirements))

    def _must_not_run(*args, **kwargs):
        raise AssertionError(
            "the web process ran the dependency scanner; it belongs in the worker"
        )

    monkeypatch.setattr(deps, "analyze_dependencies", _must_not_run)

    client = TestClient(app)
    client.cookies.set(_sessions.COOKIE_NAME, _sessions.issue(owned_job["user_id"]))
    body = client.get(f"/api/v1/deps?job_id={owned_job['job_id']}").json()

    assert body["skipped"] is False
    assert body["scanned_packages"] >= 1


@requires_postgres
def test_an_unscanned_job_says_so_rather_than_claiming_success(owned_job, live_db, monkeypatch):
    """Never report a clean bill of health for a scan that did not happen.

    A job analysed before this feature existed has no stored scan. Answering
    with score 0 and an empty vulnerability list would read as "scanned, and
    found nothing".
    """
    from fastapi.testclient import TestClient

    from archguard.dashboard import _sessions
    from archguard.dashboard.app import app
    from tests.db_fixtures import TEST_SESSION_SECRET

    monkeypatch.setenv("SESSION_SECRET", TEST_SESSION_SECRET)
    client = TestClient(app)
    client.cookies.set(_sessions.COOKIE_NAME, _sessions.issue(owned_job["user_id"]))

    body = client.get(f"/api/v1/deps?job_id={owned_job['job_id']}").json()

    assert body["skipped"] is True, "an unscanned job reported a completed scan"
    assert body["skip_reason"], "nothing told the user why there is no result"


@requires_postgres
def test_one_user_cannot_read_anothers_dependency_scan(
    owned_job, repo_with_requirements, live_db, monkeypatch
):
    """Same repository, different account: 404, not somebody else's findings."""
    from fastapi.testclient import TestClient

    from archguard.dashboard import _sessions
    from archguard.dashboard.app import app
    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.worker.tasks import scan_dependencies
    from tests.db_fixtures import TEST_SESSION_SECRET

    monkeypatch.setenv("SESSION_SECRET", TEST_SESSION_SECRET)
    _fake_pip_audit(monkeypatch)
    asyncio.run(scan_dependencies(owned_job["job_id"], repo_with_requirements))

    async def _stranger() -> int:
        async with session_scope() as session:
            user = await store.upsert_user(session, github_id=9402, login="deps-other")
            return user.id

    stranger_id = asyncio.run(_stranger())

    client = TestClient(app)
    client.cookies.set(_sessions.COOKIE_NAME, _sessions.issue(stranger_id))
    resp = client.get(f"/api/v1/deps?job_id={owned_job['job_id']}")

    assert resp.status_code == 404, (
        f"another account read this job's dependency scan (status {resp.status_code})"
    )


# ------------------------------------------------- the split, stated structurally


def test_the_web_routes_do_not_import_the_scanner():
    """A grep, deliberately.

    The behavioural test above can only catch the route reaching for the
    scanner on a path it happens to exercise. This catches it anywhere in the
    request-handling layer, which is what the web image not carrying pip-audit
    actually depends on.
    """
    from pathlib import Path as _Path

    routes = _Path(__file__).resolve().parents[2] / "archguard" / "dashboard" / "routes"
    offenders = [
        p.name
        for p in routes.glob("*.py")
        if "analyze_dependencies" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], (
        f"these request handlers call the dependency scanner: {offenders}. "
        "It runs in the worker, whose image is the one carrying pip-audit."
    )


def test_the_worker_is_what_calls_the_scanner():
    """The other half, so this cannot pass by the feature being deleted."""
    from pathlib import Path as _Path

    tasks = (
        _Path(__file__).resolve().parents[2] / "archguard" / "worker" / "tasks.py"
    ).read_text(encoding="utf-8")
    assert "analyze_dependencies" in tasks, (
        "nothing in the worker runs the dependency scan any more"
    )
