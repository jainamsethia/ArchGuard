"""Persistence operations for jobs, runs and suppressions.

Everything the dashboard used to tail-scan out of ``.archguard-cache/audit.jsonl``
now goes through here.

Read functions return plain dicts in the shape the audit log used to produce,
not ORM objects. That is deliberate for this step: the route layer, the
remediation selector, the evolution tracker and the frontend all already agree
on that shape, and changing the storage engine and the wire format in one
change would make any regression impossible to attribute. Typed models come
with the route restructure.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from archguard.db.models import (
    DependencyScan,
    FileHash,
    Job,
    JobStatus,
    Repository,
    Run,
    Suppression,
    User,
    Violation,
)

logger = logging.getLogger(__name__)


def _is_job_id(job_id: str) -> bool:
    """Whether a string could be a job id at all.

    Job ids are UUIDs and appear in browser URLs, so a user editing the address
    bar hands us arbitrary text. Postgres rejects a malformed UUID with a
    DataError, which surfaces as a 500 -- an invalid id is a 404, not a server
    fault, and the shape check belongs where every lookup passes rather than in
    each route that might forget it.
    """
    try:
        uuid.UUID(job_id)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _project_name(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")


async def upsert_user(
    session: AsyncSession,
    github_id: int,
    login: str,
    avatar_url: str | None = None,
) -> User:
    """Find or create the account behind a GitHub identity.

    Matched on ``github_id``, never on ``login``: a login can be renamed and the
    freed name registered by someone else, so matching on it would eventually
    hand one person's analysis history to another. The login and avatar are
    refreshed on every sign-in, since both change and a stale one is only ever
    wrong.
    """
    user = (
        await session.execute(select(User).where(User.github_id == github_id))
    ).scalar_one_or_none()
    if user is None:
        user = User(github_id=github_id, login=login, avatar_url=avatar_url)
        session.add(user)
        await session.flush()
        logger.info("Created account for github_id=%s", github_id)
        return user

    user.login = login
    user.avatar_url = avatar_url
    return user


async def upsert_repository(session: AsyncSession, url: str) -> Repository:
    """Find or create the row for *url*.

    One row per repository is what makes per-repository history possible at
    all: in the JSONL design nothing tied two scans of the same project
    together except a string comparison over every line ever written.
    """
    existing = (
        await session.execute(select(Repository).where(Repository.url == url))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    owner, _, name = url.removesuffix(".git").rpartition("/")
    repo = Repository(owner=owner.rsplit("/", 1)[-1], name=name, url=url)
    session.add(repo)
    await session.flush()
    return repo


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


async def create_job(
    session: AsyncSession, repo_url: str, user_id: int | None = None
) -> Job:
    repo = await upsert_repository(session, repo_url)
    job = Job(user_id=user_id, repository_id=repo.id, status="queued")
    session.add(job)
    await session.flush()
    return job


async def set_job_status(
    session: AsyncSession,
    job_id: str,
    status: str,
    error: str | None = None,
) -> None:
    if not _is_job_id(job_id):
        logger.warning("set_job_status: %r is not a job id", job_id)
        return
    job = await session.get(Job, job_id)
    if job is None:
        logger.warning("set_job_status: job %s not found", job_id)
        return
    job.status = status
    if error is not None:
        job.error = error
    if status in ("cloning", "analysing") and job.started_at is None:
        job.started_at = datetime.now(UTC)
    if status in ("complete", "failed"):
        job.completed_at = datetime.now(UTC)


async def get_job(session: AsyncSession, job_id: str, user_id: int) -> Job | None:
    """One job, if it belongs to this user.

    ``user_id`` is required rather than defaulted, on every read in this module.
    A default meaning "all users" is the kind of parameter a new call site
    forgets, and forgetting it here is a cross-tenant leak rather than a bug in
    one endpoint -- so the type checker is made to ask.

    Another user's job is reported as absent, not as forbidden: 403 confirms
    the id exists, which is exactly what an enumeration attempt wants to learn.
    """
    if not _is_job_id(job_id):
        return None
    job = await session.get(Job, job_id)
    if job is None or job.user_id != user_id:
        return None
    return job


async def get_job_repo_url(
    session: AsyncSession, job_id: str, user_id: int
) -> str | None:
    """The repository URL a job analysed, or None if the job is unknown.

    Suppressions are keyed by repository, so this lookup has to survive a
    restart -- which is exactly what the in-memory job map could not do.
    """
    if not _is_job_id(job_id):
        return None
    job = await session.get(Job, job_id)
    if job is None or job.user_id != user_id or job.repository_id is None:
        return None
    repo = await session.get(Repository, job.repository_id)
    return repo.url if repo else None


async def running_job_ids(session: AsyncSession) -> set[str]:
    """Ids of jobs that have not reached a terminal state.

    Used by the workspace sweeper to decide which clones are still in use. It
    read process memory before, which was wrong the moment the analysis moved
    to a worker: the web process has no idea what is in flight, so it would
    have swept a running job's clone out from under it.
    """
    rows = (
        await session.execute(
            select(Job.id).where(
                Job.status.in_(
                    [
                        JobStatus.QUEUED.value,
                        JobStatus.CLONING.value,
                        JobStatus.ANALYSING.value,
                    ]
                )
            )
        )
    ).scalars()
    return {str(row) for row in rows}


async def job_repo_url_unscoped(session: AsyncSession, job_id: str) -> str | None:
    """The repository a job analysed, without a user filter.

    The one deliberately unscoped read in this module, and it is named so that
    is impossible to miss in a diff. The worker consumes a job id off a queue;
    there is no request and therefore no user to scope by, and the ownership
    decision was already made when the row was created. It returns a URL and
    nothing else -- no findings, no history -- so it cannot become an
    accidental read path for user data.
    """
    if not _is_job_id(job_id):
        return None
    job = await session.get(Job, job_id)
    if job is None or job.repository_id is None:
        return None
    repo = await session.get(Repository, job.repository_id)
    return repo.url if repo else None


async def list_jobs(
    session: AsyncSession, user_id: int, limit: int = 50
) -> list[dict[str, Any]]:
    """This user's jobs, newest first.

    Unscoped, this returned every job id the instance had ever issued -- the
    enumeration step that made the rest of D1 exploitable.
    """
    rows = (
        await session.execute(
            select(Job, Repository)
            .join(Repository, Job.repository_id == Repository.id)
            .where(Job.user_id == user_id)
            .order_by(Job.created_at.desc())
            .limit(limit)
        )
    ).all()

    out: list[dict[str, Any]] = []
    for job, repo in rows:
        latest = (
            await session.execute(
                select(Run).where(Run.job_id == job.id).order_by(Run.id.desc()).limit(1)
            )
        ).scalar_one_or_none()
        out.append(
            {
                "job_id": job.id,
                "github_url": repo.url,
                "status": job.status,
                "created_at": job.created_at.isoformat(),
                "health_score": latest.health_score if latest else None,
                "health_grade": latest.health_grade if latest else None,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


async def persist_run(
    session: AsyncSession,
    job_id: str,
    payload: dict[str, Any],
    commit_sha: str | None = None,
    health_grade: str | None = None,
    composite_score: float | None = None,
) -> Run:
    """Store one completed analysis and its findings."""
    job = await session.get(Job, job_id) if _is_job_id(job_id) else None
    if job is None:
        raise ValueError(f"cannot persist a run for unknown job {job_id!r}")

    run = Run(
        job_id=job_id,
        user_id=job.user_id,
        repository_id=job.repository_id,
        commit_sha=commit_sha,
        health_score=payload.get("score"),
        health_grade=health_grade,
        composite_score=composite_score,
        band=payload.get("band"),
        skipped=bool(payload.get("skipped", False)),
        skip_reason=payload.get("skip_reason") or None,
        contract_auto_generated=bool(payload.get("contract_auto_generated", False)),
        fallback_directory_heuristic=bool(
            payload.get("fallback_directory_heuristic", False)
        ),
        fallback_reason=payload.get("fallback_reason") or "",
        derived_artifacts_error=payload.get("derived_artifacts_error") or "",
        layer_results=payload.get("layer_results") or [],
        module_scores=payload.get("module_scores") or {},
        modules_analyzed=payload.get("modules_analyzed") or [],
        dependency_graph=payload.get("dependency_graph") or {},
        import_edges=payload.get("import_edges") or [],
        contract=payload.get("contract") or {},
        metrics=payload.get("metrics") or {},
    )
    session.add(run)
    await session.flush()

    for v in payload.get("violations") or []:
        layer_raw = v.get("layer")
        try:
            layer = int(layer_raw) if layer_raw not in (None, "") else None
        except (TypeError, ValueError):
            layer = None
        session.add(
            Violation(
                run_id=run.id,
                layer=layer,
                severity=str(v.get("severity") or "low"),
                kind=str(v.get("kind") or ""),
                module=v.get("module"),
                file=v.get("file"),
                line=v.get("line"),
                message=str(v.get("message") or ""),
                scope=str(v.get("scope") or "file"),
                metrics=v.get("metrics") or {},
            )
        )
    await session.flush()
    return run


def run_to_dict(run: Run, repo: Repository, violations: list[Violation]) -> dict[str, Any]:
    """Render a run in the shape every existing consumer already reads."""
    return {
        "timestamp": run.created_at.isoformat(),
        "event": "analysis_run",
        "job_id": run.job_id,
        "repo_url": repo.url,
        "project_name": _project_name(repo.url),
        "commit_sha": run.commit_sha,
        "score": run.health_score,
        "grade": run.health_grade,
        "band": run.band,
        "skipped": run.skipped,
        "skip_reason": run.skip_reason or "",
        "violations": [
            {
                "file": v.file,
                "line": v.line,
                "module": v.module,
                "severity": v.severity,
                "message": v.message,
                # str: the frontend compares it against the values of a <select>
                "layer": str(v.layer) if v.layer is not None else "",
                "scope": v.scope,
                "kind": v.kind,
                "metrics": v.metrics,
            }
            for v in violations
        ],
        "layer_results": run.layer_results,
        "module_scores": run.module_scores,
        "modules_analyzed": run.modules_analyzed,
        "dependency_graph": run.dependency_graph,
        "import_edges": run.import_edges,
        "contract": run.contract,
        "metrics": run.metrics,
        "contract_auto_generated": run.contract_auto_generated,
        "fallback_directory_heuristic": run.fallback_directory_heuristic,
        "fallback_reason": run.fallback_reason,
        "derived_artifacts_error": run.derived_artifacts_error,
    }


async def _hydrate(session: AsyncSession, runs: list[Run]) -> list[dict[str, Any]]:
    if not runs:
        return []
    repo_ids = {r.repository_id for r in runs}
    repos = {
        r.id: r
        for r in (
            await session.execute(select(Repository).where(Repository.id.in_(repo_ids)))
        ).scalars()
    }
    run_ids = [r.id for r in runs]
    by_run: dict[int, list[Violation]] = {rid: [] for rid in run_ids}
    for v in (
        await session.execute(select(Violation).where(Violation.run_id.in_(run_ids)))
    ).scalars():
        by_run[v.run_id].append(v)
    return [run_to_dict(r, repos[r.repository_id], by_run.get(r.id, [])) for r in runs]


async def get_latest_run(
    session: AsyncSession, job_id: str, user_id: int
) -> dict[str, Any] | None:
    if not _is_job_id(job_id):
        return None
    run = (
        await session.execute(
            select(Run)
            .where(Run.job_id == job_id, Run.user_id == user_id)
            .order_by(Run.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if run is None:
        return None
    return (await _hydrate(session, [run]))[0]


async def get_runs_for_job(
    session: AsyncSession, job_id: str, user_id: int, limit: int = 50
) -> list[dict[str, Any]]:
    if not _is_job_id(job_id):
        return []
    runs = list(
        (
            await session.execute(
                select(Run)
                .where(Run.job_id == job_id, Run.user_id == user_id)
                .order_by(Run.id.desc())
                .limit(limit)
            )
        ).scalars()
    )
    return list(reversed(await _hydrate(session, runs)))


async def get_recent_runs(
    session: AsyncSession, user_id: int, limit: int = 50
) -> list[dict[str, Any]]:
    """This user's most recent runs, newest first, across their repositories."""
    runs = list(
        (
            await session.execute(
                select(Run)
                .where(Run.user_id == user_id)
                .order_by(Run.id.desc())
                .limit(limit)
            )
        ).scalars()
    )
    return await _hydrate(session, runs)


async def get_runs_for_repository(
    session: AsyncSession, repo_url: str, user_id: int, limit: int = 50
) -> list[dict[str, Any]]:
    """Every run recorded for one repository, newest first.

    This is the query the JSONL store could not answer, which is why the trend
    chart showed "not enough scan history" for every repository no matter how
    many times it had been analysed: each job cloned fresh and wrote one run to
    a file nothing correlated by project.
    """
    repo = (
        await session.execute(select(Repository).where(Repository.url == repo_url))
    ).scalar_one_or_none()
    if repo is None:
        return []
    runs = list(
        (
            await session.execute(
                select(Run)
                .where(Run.repository_id == repo.id, Run.user_id == user_id)
                .order_by(Run.id.desc())
                .limit(limit)
            )
        ).scalars()
    )
    return await _hydrate(session, runs)


# ---------------------------------------------------------------------------
# Dependency scans
# ---------------------------------------------------------------------------


async def save_dependency_scan(
    session: AsyncSession, job_id: str, user_id: int, payload: dict[str, Any]
) -> None:
    """Store a scan, but only against a job this user owns.

    Checked rather than assumed: the scan is triggered by a request carrying a
    job id, so an unchecked write would let anyone attach data to a stranger's
    job.
    """
    if await get_job(session, job_id, user_id) is None:
        raise ValueError(f"cannot store a dependency scan for unknown job {job_id!r}")
    session.add(DependencyScan(job_id=job_id, payload=payload))


async def get_dependency_scan(
    session: AsyncSession, job_id: str, user_id: int
) -> dict[str, Any] | None:
    if not _is_job_id(job_id):
        return None
    row = (
        await session.execute(
            select(DependencyScan)
            .join(Job, DependencyScan.job_id == Job.id)
            .where(DependencyScan.job_id == job_id, Job.user_id == user_id)
            .order_by(DependencyScan.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return dict(row.payload) if row is not None else None


# ---------------------------------------------------------------------------
# Suppressions
# ---------------------------------------------------------------------------


async def list_suppressions(
    session: AsyncSession, repo_url: str, include_inactive: bool = True
) -> list[Suppression]:
    repo = (
        await session.execute(select(Repository).where(Repository.url == repo_url))
    ).scalar_one_or_none()
    if repo is None:
        return []
    stmt = select(Suppression).where(Suppression.repository_id == repo.id)
    if not include_inactive:
        stmt = stmt.where(Suppression.active.is_(True))
    return list((await session.execute(stmt.order_by(Suppression.created_at.desc()))).scalars())


async def add_suppression(
    session: AsyncSession,
    repo_url: str,
    module: str,
    layer: int,
    violation_hash: str,
    reason: str,
    expires_at: datetime | None = None,
    pr_number: int | None = None,
    commit_sha: str = "",
    user_id: int | None = None,
) -> Suppression:
    repo = await upsert_repository(session, repo_url)
    suppression = Suppression(
        user_id=user_id,
        repository_id=repo.id,
        module=module,
        layer=layer,
        violation_hash=violation_hash,
        reason=reason,
        expires_at=expires_at,
        pr_number=pr_number,
        commit_sha=commit_sha,
    )
    session.add(suppression)
    await session.flush()
    return suppression


async def delete_suppression(session: AsyncSession, suppression_id: str) -> bool:
    """True if a row was removed. False means the id did not exist, which the
    route turns into a 404 rather than reporting a successful no-op."""
    existing = await session.get(Suppression, suppression_id)
    if existing is None:
        return False
    await session.delete(existing)
    return True


async def get_previous_run_for_job(
    session: AsyncSession, job_id: str
) -> dict[str, Any] | None:
    """The most recent earlier run of the same repository, by the same user.

    Same user, deliberately. File hashes are content digests of a public
    repository and are safe to share, but findings belong to whoever ran the
    analysis: carrying one account's violations into another's report would be
    a tenancy leak dressed up as a cache hit.
    """
    job = await session.get(Job, job_id)
    if job is None or job.repository_id is None:
        return None

    query = (
        select(Run)
        .where(Run.repository_id == job.repository_id, Run.job_id != job_id)
        .order_by(Run.id.desc())
        .limit(1)
    )
    if job.user_id is None:
        query = query.where(Run.user_id.is_(None))
    else:
        query = query.where(Run.user_id == job.user_id)

    run = (await session.execute(query)).scalars().first()
    if run is None:
        return None
    hydrated = await _hydrate(session, [run])
    return hydrated[0] if hydrated else None


async def load_file_hashes(session: AsyncSession, repository_id: int) -> dict[str, str]:
    """Content hashes recorded for a repository, keyed by repo-relative path.

    Keyed by repository rather than by job: the point is to survive the clone,
    which is deleted after every job.
    """
    rows = (
        await session.execute(
            select(FileHash).where(FileHash.repository_id == repository_id)
        )
    ).scalars()
    return {row.path: row.sha256 for row in rows}


async def save_file_hashes(
    session: AsyncSession, repository_id: int, records: dict[str, str]
) -> None:
    """Replace a repository's recorded hashes with what this scan measured.

    Replace, not merge: a path absent from `records` was deleted or is no
    longer analysed, and leaving its hash behind would let a file reappear
    later and be called unchanged against a hash from before it vanished.

    One statement per operation inside the caller's transaction, so a failure
    part-way leaves the previous hashes intact -- a stale complete cache is
    safe, whereas a half-written one would call edited files unchanged.
    """
    await session.execute(
        delete(FileHash).where(FileHash.repository_id == repository_id)
    )
    if not records:
        return
    session.add_all(
        [
            FileHash(repository_id=repository_id, path=path, sha256=sha)
            for path, sha in records.items()
        ]
    )
    await session.flush()


async def active_violation_hashes(session: AsyncSession, repo_url: str) -> set[str]:
    """Hashes to filter out of a run, for the repository being analysed.

    Expiry is applied here rather than by a sweep, so an expired suppression
    stops taking effect at the moment it expires rather than whenever something
    next happens to tidy up.
    """
    now = datetime.now(UTC)
    return {
        s.violation_hash
        for s in await list_suppressions(session, repo_url, include_inactive=False)
        if s.expires_at is None or s.expires_at > now
    }
