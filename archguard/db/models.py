"""Relational schema for ArchGuard.

Replaces a single append-only ``.archguard-cache/audit.jsonl`` that every read
endpoint tail-scanned and filtered in Python. That file was capped at 10 MB and
truncated itself when it grew past it, so the only durable record of every
analysis anyone had ever run silently deleted its own history (C9).

Two things shape the design.

*Every row is owned.* ``user_id`` is on jobs, runs and suppressions from the
start, even though accounts do not exist yet, because retrofitting ownership
onto rows already in production is the part that goes wrong. The tenancy work
adds the ``users`` rows and the filters; the columns are already here.

*JSONB, not columns, for the analysis payload.* ``layer_results``,
``module_scores``, ``dependency_graph``, ``import_edges``, ``contract`` and
``metrics`` are shapes the analysis engine owns and changes. Modelling them as
tables would mean a migration every time a layer gains a field, and the
dashboard reads them as opaque blobs anyway. What *is* promoted to columns is
everything something filters, sorts or aggregates on.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base. Alembic autogenerate reads metadata from here."""


def _now() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class JobStatus(str, Enum):
    """The values the ``jobs.status`` column takes.

    A str Enum so a comparison against a plain string from the database still
    works, and so it serialises without a converter. Defined here rather than in
    the dashboard because the worker writes these values and the web process
    only reads them -- neither owns the vocabulary.
    """

    QUEUED = "queued"
    CLONING = "cloning"
    ANALYSING = "analysing"
    COMPLETE = "complete"
    FAILED = "failed"

    @classmethod
    def is_terminal(cls, status: str | None) -> bool:
        return status in (cls.COMPLETE.value, cls.FAILED.value)

    @classmethod
    def is_running(cls, status: str | None) -> bool:
        return status in (cls.QUEUED.value, cls.CLONING.value, cls.ANALYSING.value)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: GitHub's numeric account id, not the login: logins can be renamed and
    #: reused, ids cannot.
    github_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    login: Mapped[str] = mapped_column(String(39), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _now()

    jobs: Mapped[list[Job]] = relationship(back_populates="user")


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str] = mapped_column(String(39), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    #: The canonical clone URL, rebuilt from validated parts -- never raw input.
    url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_at: Mapped[datetime] = _now()

    jobs: Mapped[list[Job]] = relationship(back_populates="repository")


class Job(Base):
    """One submitted analysis.

    The id stays a UUID because it is handed to the browser and appears in URLs;
    a sequential id would let anyone enumerate other people's analyses.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    #: Only messages this application composed. An arbitrary exception string
    #: carries server paths and module structure, and this reaches the browser.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User | None] = relationship(back_populates="jobs")
    repository: Mapped[Repository] = relationship(back_populates="jobs")
    runs: Mapped[list[Run]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class Run(Base):
    """The result of one completed analysis."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)

    #: 0-100, higher is better. The inverse of composite_score.
    health_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    health_grade: Mapped[str | None] = mapped_column(String(1), nullable=True)
    #: 0.0-1.0 ArchDebt, higher is worse.
    composite_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    band: Mapped[str | None] = mapped_column(String(16), nullable=True)

    skipped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Provenance of the module map every score above is computed against. When
    #: fallback_directory_heuristic is true the boundaries were guessed from
    #: directory names, not measured from co-change history, and the whole
    #: result has to be read with that caveat.
    contract_auto_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    fallback_directory_heuristic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    fallback_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    derived_artifacts_error: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )

    layer_results: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    module_scores: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    modules_analyzed: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    dependency_graph: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    import_edges: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    contract: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = _now()

    job: Mapped[Job] = relationship(back_populates="runs")
    violations: Mapped[list[Violation]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # The dashboard's most common read: this repository's runs, newest
        # first, for one user.
        Index("ix_runs_user_repo_created", "user_id", "repository_id", "created_at"),
    )


class Violation(Base):
    """One finding. A row rather than JSONB because these are filtered,
    counted, and grouped by layer and severity."""

    __tablename__ = "violations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    layer: Mapped[int | None] = mapped_column(Integer, nullable=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Structured form of the facts already in `message`, for plain-language
    #: rendering. Empty for runs persisted before it existed.
    kind: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    module: Mapped[str | None] = mapped_column(Text, nullable=True)
    file: Mapped[str | None] = mapped_column(Text, nullable=True)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="file")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    run: Mapped[Run] = relationship(back_populates="violations")


class Suppression(Base):
    """A deliberately ignored finding.

    Keyed by repository and owner. Not by job, because every scan creates a
    new job id and a job-scoped suppression could never be found again on the
    next scan -- which is the only time one is any use. And not by repository
    alone, because that was shared by every account that had analysed it.
    """

    __tablename__ = "suppressions"

    id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    #: Required. A suppression without an owner is invisible to every scoped
    #: query -- which fails safe, but silently, and a row nobody can see or
    #: delete is not a state worth being able to reach. Keyed by repository AND
    #: owner because two accounts analysing the same public repository are not
    #: collaborating: which findings a team has chosen to ignore, and why, is
    #: not public information just because the code is.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    module: Mapped[str] = mapped_column(Text, nullable=False)
    layer: Mapped[int] = mapped_column(Integer, nullable=False)
    #: sha256 of (module, layer, message) -- what matching actually keys on.
    violation_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class DependencyScan(Base):
    """A pip-audit result for one job.

    Its own table rather than a column on ``runs``: the scan is triggered
    separately, after the run, and folding it in would mean a run row that is
    rewritten later -- or, as in the JSONL design, a second "run" entry that
    polluted run history and trend charts.
    """

    __tablename__ = "dependency_scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _now()


class FileHash(Base):
    """Content hash per file per repository, for incremental re-analysis.

    The CLI kept this in a JSON file at the analysed repository's root. The web
    application analyses a throwaway clone that is deleted after every job, so
    the cache could never survive to be useful. Keyed by repository so it does.
    """

    __tablename__ = "file_hashes"

    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), primary_key=True
    )
    path: Mapped[str] = mapped_column(Text, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )


class WatchedRepository(Base):
    """A repository a user has asked to be rescanned and told about.

    One row per (user, repository): watching is a personal decision, and two
    people watching the same public repository want their own thresholds, their
    own webhook and their own alert history.

    `last_alert_key` is what makes alerting safe under retries. A worker that
    dies after sending an alert but before recording it would, on the next
    attempt, send the same alert again -- so the key is derived from the run and
    the regression itself rather than from a flag set in memory. Re-sending is
    skipped when the key matches, which survives a restart because it lives here
    rather than in the process.
    """

    __tablename__ = "watched_repositories"
    __table_args__ = (
        # One watch per user per repository. Without it a double-submit leaves
        # two rows and the repository is scanned -- and alerted on -- twice.
        UniqueConstraint("user_id", "repository_id", name="uq_watch_user_repo"),
        # The cron's query: every active watch, oldest check first.
        Index("ix_watch_due", "active", "last_checked_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: Paused rather than deleted, so a user keeps their threshold and history.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    #: Only "daily" is honoured today. A column rather than a boolean so adding
    #: another cadence later is a value, not a migration.
    schedule: Mapped[str] = mapped_column(String(16), nullable=False, default="daily")

    #: How far health must fall between scans before it is worth telling
    #: someone. Per watch: a repository under active refactoring and one in
    #: maintenance do not deserve the same sensitivity.
    health_drop_threshold: Mapped[float] = mapped_column(
        Float, nullable=False, default=5.0
    )

    #: Where to POST an alert. Validated against the SSRF guard before use, on
    #: the way in and again before every send.
    webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_alert_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Deterministic identity of the last regression alerted on. See the class
    #: docstring: this is the duplicate-alert guard.
    last_alert_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: What the last regression was, for the dashboard to show without
    #: recomputing it.
    last_status: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
