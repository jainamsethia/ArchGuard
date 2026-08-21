"""Asyncio job queue for repository analysis.

Jobs are recorded in PostgreSQL; the in-memory mirror here exists only to carry
live progress to the SSE stream, and is replaced when the queue worker lands.
Anything the dashboard reads back -- status, results, history -- comes from the
database, so a restart no longer loses every submitted job.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from archguard.dashboard.routes.jobs import build_safe_clone_url, parse_github_url

logger = logging.getLogger(__name__)

MAX_STORED_JOBS = 50
MAX_CONCURRENT_ANALYSES = 3   # semaphore limit

class JobStatus(str, Enum):
    QUEUED    = "queued"
    CLONING   = "cloning"
    ANALYSING = "analysing"
    COMPLETE  = "complete"
    FAILED    = "failed"

@dataclass
class AnalysisJob:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    github_url: str = ""
    status: JobStatus = JobStatus.QUEUED
    progress_messages: list[str] = field(default_factory=list)
    result: Any = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    _cached_result_dict: dict | None = field(default=None, repr=False)  # type: ignore[type-arg]

class JobManager:
    """Job store with asyncio background execution.

    Usage::

        job = await job_manager.create_job("https://github.com/owner/repo")
        task = asyncio.create_task(job_manager.run_job(job))
        job_manager.track_task(job.id, task)
    """
    def __init__(self) -> None:
        self._jobs: dict[str, AnalysisJob] = {}
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        # Initialise semaphore to None; created on first async access via _ensure_semaphore().
        # Semaphore cannot be created in __init__ because JobManager may be instantiated
        # before an event loop is running (module import time).
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_lock: asyncio.Lock | None = None

    def track_task(self, job_id: str, task: asyncio.Task[None]) -> None:
        """Record the running task for a job so it can be cancelled on shutdown."""
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(job_id, None))

    async def cancel_all_running(self, timeout: float = 5.0) -> int:
        """Cancel every still-running job task. Returns the number cancelled."""
        pending = list(self._tasks.values())
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.wait(pending, timeout=timeout)
        return len(pending)

    async def _ensure_semaphore(self) -> asyncio.Semaphore:
        """Return the semaphore, creating it atomically on first call."""
        if self._semaphore is not None:
            return self._semaphore
        # Create an asyncio.Lock if needed (also lazy for same reason)
        if self._semaphore_lock is None:
            self._semaphore_lock = asyncio.Lock()
        async with self._semaphore_lock:
            if self._semaphore is None:
                self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_ANALYSES)
        return self._semaphore

    async def create_job(self, github_url: str, user_id: int | None = None) -> AnalysisJob:
        """Create a QUEUED job in PostgreSQL and mirror it in memory.

        The database row is the record of the job; the in-memory copy exists
        only to carry live progress to the SSE stream, and goes away when the
        queue worker lands. The id comes from the row so both agree.
        """
        from archguard.db.session import session_scope
        from archguard.db.store import create_job as db_create_job

        async with session_scope() as session:
            db_job = await db_create_job(session, github_url, user_id=user_id)
            job_id = db_job.id

        job = AnalysisJob(id=job_id, github_url=github_url)
        self._jobs[job.id] = job
        logger.info("Created job %s for %s", job.id, github_url)

        # Evict oldest jobs to stay within memory limit
        if len(self._jobs) > MAX_STORED_JOBS:
            evictable = [jid for jid, j in self._jobs.items() if j.status not in (JobStatus.QUEUED, JobStatus.CLONING, JobStatus.ANALYSING)]
            if evictable:
                # evictable is ordered by insertion (python 3.7+ dict order), so evictable[0] is oldest completed/failed
                oldest_id = evictable[0]
                del self._jobs[oldest_id]
                logger.debug("Evicted old job %s", oldest_id)
                # Immediately reclaim disk space
                import shutil
                import tempfile
                from pathlib import Path
                workspace_dir = Path(tempfile.gettempdir()) / f"archguard-{oldest_id}"
                if workspace_dir.exists():
                    shutil.rmtree(workspace_dir, ignore_errors=True)
            else:
                logger.debug("All jobs are active; skipping eviction this cycle rather than killing a live job")
        return job

    def get_job(self, job_id: str) -> AnalysisJob | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[AnalysisJob]:
        """Return jobs sorted newest-first."""
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    async def _record_status(self, job_id: str, status: str, error: str | None = None) -> None:
        """Mirror a status change into PostgreSQL. Never raises: losing the
        status update must not also abort the analysis."""
        try:
            from archguard.db.session import session_scope
            from archguard.db.store import set_job_status

            async with session_scope() as session:
                await set_job_status(session, job_id, status, error=error)
        except Exception:
            logger.exception("[job %s] Could not record status %s", job_id, status)

    async def run_job(self, job: AnalysisJob) -> None:
        """Execute the full clone + analysis pipeline for a job.

        Call via asyncio.create_task() or FastAPI BackgroundTasks.
        Acquires the semaphore to limit concurrent analyses.
        """
        from archguard.dashboard.pipeline_adapter import run_analysis_on_repo
        from archguard.dashboard.workspace import enforce_workspace_budget, temp_workspace
        from archguard.observability.logger import correlation_id_var

        # asyncio.create_task copies the caller's context, so without this the
        # job inherits the correlation id of the HTTP request that merely
        # queued it -- and keeps it for the whole analysis, minutes after that
        # request returned. Re-bind to the job's own id. No reset: the task's
        # context is a copy, discarded when the task ends.
        correlation_id_var.set(job.id[:8])

        async def send_progress(msg: str) -> None:
            job.progress_messages.append(f"[{datetime.now(UTC).strftime('%H:%M:%S')}] {msg}")
            logger.debug("[job %s] %s", job.id, msg)

        semaphore = await self._ensure_semaphore()
        async with semaphore:
            try:
                # -- Clone phase ----------------------------------------
                job.status = JobStatus.CLONING
                await self._record_status(job.id, JobStatus.CLONING.value)

                # Reclaim disk before adding to it. Only jobs that are still
                # running are protected: a finished job's clone is a cache for
                # browsing results, and the read endpoints already fall back to
                # the persisted run once it is gone.
                running = {
                    j.id
                    for j in self.list_jobs()
                    if j.status
                    in (JobStatus.QUEUED, JobStatus.CLONING, JobStatus.ANALYSING)
                }
                evicted, reclaimed = await enforce_workspace_budget(
                    active_job_ids=running
                )
                if evicted:
                    logger.info(
                        "Evicted %d workspace(s) reclaiming %d bytes before cloning",
                        evicted,
                        reclaimed,
                    )

                await send_progress(f"Cloning {job.github_url}...")

                # Reconstruct safe URL from validated parts - never clone raw user input
                _owner, _repo_name = parse_github_url(job.github_url)
                clone_url = build_safe_clone_url(_owner, _repo_name)

                async with temp_workspace(clone_url, job_id=job.id, keep_alive=True) as repo_path:
                    # -- Analysis phase ---------------------------------
                    job.status = JobStatus.ANALYSING
                    await self._record_status(job.id, JobStatus.ANALYSING.value)
                    await send_progress("Repository cloned. Starting analysis...")

                    result = await run_analysis_on_repo(
                        repo_path=repo_path,
                        job_id=job.id,
                        repo_url=job.github_url,
                        progress_callback=send_progress,
                        skip_explanation=True,   # LLM explanation skipped by default for speed
                    )

                    job.result = result
                    job.status = JobStatus.COMPLETE
                    job.completed_at = datetime.now(UTC)
                    await self._record_status(job.id, JobStatus.COMPLETE.value)
                    job._cached_result_dict = asdict(job.result)
                    await send_progress(
                        f"Done. Health: {result.health_score:.1f}/100 "
                        f"({result.health_grade}) · "
                        f"Violations: {result.total_violations} · "
                        f"Duration: {result.duration_seconds}s"
                    )

            except TimeoutError as exc:
                job.status = JobStatus.FAILED
                job.error = str(exc)
                job.completed_at = datetime.now(UTC)
                await self._record_status(job.id, JobStatus.FAILED.value, error=job.error)
                logger.exception("[job %s] Clone timeout: %s", job.id, exc)

            except ValueError:
                # parse_github_url. A user input error, so say what is wrong --
                # but without echoing the input back into the page.
                job.status = JobStatus.FAILED
                job.error = (
                    "Cannot parse GitHub URL. "
                    "Expected format: https://github.com/owner/repo"
                )
                job.completed_at = datetime.now(UTC)
                await self._record_status(job.id, JobStatus.FAILED.value, error=job.error)
                logger.warning("[job %s] Malformed GitHub URL rejected", job.id)

            except Exception as exc:
                job.status = JobStatus.FAILED
                # job.error is rendered verbatim in the browser. Only messages
                # this code composed are safe to put there; an arbitrary
                # exception string carries server filesystem paths, temp
                # directory names and internal module structure.
                if (
                    "git clone failed" in str(exc).lower()
                    or getattr(exc, "returncode", None) is not None
                ):
                    job.error = (
                        "Repository cloning failed. Ensure the URL is correct, "
                        "public, and reachable."
                    )
                else:
                    job.error = (
                        "Analysis failed unexpectedly. The server logs record "
                        f"the cause under job {job.id}."
                    )
                job.completed_at = datetime.now(UTC)
                await self._record_status(job.id, JobStatus.FAILED.value, error=job.error)
                logger.exception("[job %s] Unexpected failure", job.id)


# Module-level singleton - imported by routes
job_manager = JobManager()
