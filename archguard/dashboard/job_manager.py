"""In-memory async job queue for ArchGuard repository analysis.
V1 uses asyncio for concurrency (no Redis or Celery required).
Jobs persist in memory; the last 50 are kept.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from archguard.dashboard.routes.jobs import parse_github_url, build_safe_clone_url

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
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    _cached_result_dict: dict | None = field(default=None, repr=False)  # type: ignore[type-arg]

class JobManager:
    """Thread-safe in-memory job store with asyncio background execution.
    
    Usage:
        job = job_manager.create_job("https://github.com/owner/repo")
        asyncio.create_task(job_manager.run_job(job))
    """
    def __init__(self) -> None:
        self._jobs: dict[str, AnalysisJob] = {}
        # Initialise semaphore to None; created on first async access via _ensure_semaphore().
        # Semaphore cannot be created in __init__ because JobManager may be instantiated
        # before an event loop is running (module import time).
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_lock: asyncio.Lock | None = None

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

    def create_job(self, github_url: str) -> AnalysisJob:
        """Create and store a new QUEUED job. Evicts oldest jobs beyond MAX_STORED_JOBS."""
        job = AnalysisJob(github_url=github_url)
        self._jobs[job.id] = job
        logger.info("Created job %s for %s", job.id, github_url)

        # Evict oldest jobs to stay within memory limit
        if len(self._jobs) > MAX_STORED_JOBS:
            oldest_id = min(self._jobs, key=lambda jid: self._jobs[jid].created_at)
            del self._jobs[oldest_id]
            logger.debug("Evicted old job %s", oldest_id)
        return job

    def get_job(self, job_id: str) -> AnalysisJob | None:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[AnalysisJob]:
        """Return jobs sorted newest-first."""
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    async def run_job(self, job: AnalysisJob) -> None:
        """Execute the full clone + analysis pipeline for a job.
        
        Call via asyncio.create_task() or FastAPI BackgroundTasks.
        Acquires the semaphore to limit concurrent analyses.
        """
        from archguard.dashboard.workspace import temp_workspace
        from archguard.dashboard.pipeline_adapter import run_analysis_on_repo

        async def send_progress(msg: str) -> None:
            job.progress_messages.append(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}")
            logger.debug("[job %s] %s", job.id, msg)

        semaphore = await self._ensure_semaphore()
        async with semaphore:
            try:
                # -- Clone phase ----------------------------------------
                job.status = JobStatus.CLONING
                await send_progress(f"Cloning {job.github_url}...")

                # Reconstruct safe URL from validated parts - never clone raw user input
                _owner, _repo_name = parse_github_url(job.github_url)
                clone_url = build_safe_clone_url(_owner, _repo_name)

                async with temp_workspace(clone_url, job_id=job.id, keep_alive=False) as repo_path:
                    # -- Analysis phase ---------------------------------
                    job.status = JobStatus.ANALYSING
                    await send_progress("Repository cloned. Starting analysis...")

                    result = await run_analysis_on_repo(
                        repo_path=repo_path,
                        job_id=job.id,
                        repo_url=job.github_url,
                        progress_callback=send_progress,
                        skip_explanation=True,   # LLM explanation skipped by default for speed
                    )

                    # -- Merge temporary run into global dashboard ------
                    from archguard.dashboard.app import get_audit_path
                    import json
                    source_runs = repo_path / ".archguard-cache" / "audit.jsonl"
                    if source_runs.exists():
                        global_runs = get_audit_path()
                        global_runs.parent.mkdir(parents=True, exist_ok=True)
                        with open(source_runs, "r", encoding="utf-8") as f_src:
                            lines = [line for line in f_src if line.strip()]
                            if lines:
                                try:
                                    last_run = json.loads(lines[-1])
                                    last_run["project_name"] = job.github_url.split("/")[-1].replace(".git", "")
                                    last_run["repo_url"] = job.github_url
                                    last_run["job_id"] = job.id
                                    with open(global_runs, "a", encoding="utf-8") as f_dest:
                                        f_dest.write(json.dumps(last_run) + "\n")
                                except Exception as e:
                                    logger.error("Failed to copy run to global dashboard: %s", e)

                    job.result = result
                    job.status = JobStatus.COMPLETE
                    job.completed_at = datetime.now(timezone.utc)
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
                job.completed_at = datetime.now(timezone.utc)
                logger.error("[job %s] Clone timeout: %s", job.id, exc)

            except Exception as exc:
                job.status = JobStatus.FAILED
                job.error = str(exc)
                job.completed_at = datetime.now(timezone.utc)
                logger.exception("[job %s] Unexpected failure", job.id)


# Module-level singleton - imported by routes
job_manager = JobManager()
