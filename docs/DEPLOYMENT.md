# ArchGuard Production Deployment Guide

**Version:** 1.0  
**Applies to:** `archguard>=1.0.0`  
**Last updated:** 2026-07-21

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Deployment Options](#deployment-options)
3. [Environment Configuration](#environment-configuration)
4. [Release Checklist](#release-checklist)
5. [Rollback Procedure](#rollback-procedure)
6. [Operational Runbook](#operational-runbook)
7. [Monitoring Recommendations](#monitoring-recommendations)
8. [Backup and Recovery](#backup-and-recovery)
9. [Troubleshooting](#troubleshooting)

---

## Prerequisites

- **Python 3.11+** (runtime) or **Docker** (containerized)
- **Git** (required for analysis and clone operations)
- **GITHUB_OAUTH_CLIENT_ID / SECRET, SESSION_SECRET, REDIS_URL** (a production
  instance refuses to start without them -- see the production gate below)
- **ARCHGUARD_DASHBOARD_TOKEN** (optional: an operator credential for reaching
  the API without a browser. Users sign in with GitHub and are authenticated by
  their session, so this is not needed for the product to work.)

### Optional Dependencies

| Dependency | Purpose | When Required |
|-----------|---------|---------------|
| `pip-audit` | Dependency vulnerability scanning | Self-analysis / dashboard deps tab |
| `sentence-transformers`, `faiss-cpu`, etc. | Layer 3 semantic drift analysis | Full 4-layer analysis |
| `anthropic` SDK | AI Advisor streaming | AI Advisor feature |
| `Playwright` + browsers | Visual regression tests | UI development |

---

## Deployment Options

### Option A: Docker (Production — Recommended)

```bash
# 1. Clone
git clone https://github.com/jainamsethia/ArchGuard.git
cd archguard

# 2. Configure
cp .env.example .env
# Edit .env — set ARCHGUARD_DASHBOARD_TOKEN at minimum
#   python -c "import secrets; print(secrets.token_hex(32))"

# 3. Build and start
docker compose up --build -d

# 4. Verify
curl http://localhost:8000/health
# → {"status":"ok","version":"1.0.0","environment":"production","uptime_seconds":42}
```

### Two processes, not one

Every deployment runs **two** services from the same repository:

| Service | Image | Command | Serves a port |
|---|---|---|---|
| web | `--target web` | `uvicorn archguard.dashboard.app:app` | yes, `/ready` |
| worker | `--target worker` | `arq archguard.worker.main.WorkerSettings` | no |

This is not optional and it fails quietly if you skip it. `queue_available()`
returns true as soon as `REDIS_URL` is set, so a deployment with Redis and no
worker accepts submissions, hands back a job id, streams a progress bar, and
never analyses anything — the queue just grows. There is no error to see.

The two images are deliberately different. The worker carries torch, faiss and
`pip-audit` with the embedding model baked in; the web image carries none of
them, because the web process never loads a model and torch is larger than
everything else combined. `docker build .` gives you the web image. Select the
worker with either `--target worker` or `--build-arg ARCHGUARD_IMAGE=worker`,
whichever your platform supports.

### Option B: Render (Managed Platform)

`render.yaml` is a Blueprint declaring both services plus the database and
Redis. Fork the repository, then Render Dashboard → New → Blueprint → connect
your fork.

Set these as secrets on **both** services: `SESSION_SECRET`,
`ARCHGUARD_DASHBOARD_TOKEN`, `GITHUB_TOKEN`, `GEMINI_API_KEY`. The first two
must be *identical* across the services — the audit trail is signed with them,
and two processes signing with different keys write entries that cannot be
verified against each other. `DATABASE_URL` and `REDIS_URL` are wired by
reference in the blueprint and need no manual entry. The web service also takes
`ALLOWED_ORIGINS`.

The health check is `/ready`, not `/health`: a check that returns 200 whenever
the process is alive reports a service as healthy while its database is
unreachable and every request is failing, which stops the platform rolling
back. The worker has no health check because it serves no port.

> **Needs verification on the provider.** Render builds a Dockerfile path
> without naming a build stage, so the worker service selects its image through
> the `ARCHGUARD_IMAGE=worker` build variable in `render.yaml`. That the
> blueprint is well-formed and names the right command is checked by
> `tests/unit/test_deployment_config.py`; that Render passes the variable to
> the build has not been confirmed against a live deploy. Check the worker's
> first build log for `--extras "worker"` before relying on Layers 3 and 4. If
> it built the slim image, deploy the worker from a pre-built image instead
> (`runtime: image`) — CI already builds both.

### Option C: Railway

Railway takes one configuration file per service, so this is two services from
the same repository: the web service uses `railway.toml`, the worker uses
`railway.worker.toml` (Service → Settings → Config-as-code).

Add the Postgres and Redis plugins, then set `DATABASE_URL` and `REDIS_URL` on
both services as `${{Postgres.DATABASE_URL}}` and `${{Redis.REDIS_URL}}`, plus
the same secrets listed under Render. Railway sets `PORT` for the web service
automatically.

### Option D: Bare Metal / VPS

Two processes, from a checkout rather than an image:

```bash
poetry install --extras worker          # the worker host needs the ML extras
export ENVIRONMENT=production
export DATABASE_URL=postgresql+asyncpg://...
export REDIS_URL=redis://...
export SESSION_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")

alembic upgrade head                    # once, before either process starts
uvicorn archguard.dashboard.app:app --host 0.0.0.0 --port 8000   # web host
arq archguard.worker.main.WorkerSettings                         # worker host
```

Run one uvicorn worker per process, and scale with more processes behind a load
balancer rather than with `--workers`. The web process holds no job state now,
so either works, but `--workers` shares nothing useful and complicates the
readiness signal.

---

## Environment Configuration

### Required

| Variable | Description | Default |
|----------|-------------|---------|
| `ARCHGUARD_DASHBOARD_TOKEN` | Operator credential for reaching the API with no browser, and the signing key for SSE stream tokens. Generate with `secrets.token_hex(32)` | (none — optional; signed-in users are unaffected by its absence) |

### Security

| Variable | Description | Default |
|----------|-------------|---------|
| `ARCHGUARD_DASHBOARD_ALLOW_REMOTE` | Set to `1` to allow unauthenticated remote access (NOT recommended) | (unset) |
| `ARCHGUARD_TRUSTED_PROXY_IPS` | Comma-separated proxy IPs/CIDRs for X-Forwarded-For | (none) |
| `ARCHGUARD_TRUSTED_PROXY_HOPS` | Trusted proxies in front of the app; the client IP is the Nth X-Forwarded-For entry from the right | `1` |
| `ALLOWED_ORIGINS` | CORS allowed origins | `http://localhost:3000,http://localhost:8000,http://127.0.0.1:8000` |
| `ENVIRONMENT` | `production` enables Secure cookie flag + HSTS | `development` |

### Performance

| Variable | Description | Default |
|----------|-------------|---------|
| `ARCHGUARD_CLONE_TIMEOUT` | Git clone timeout (seconds) | `120` |
| `ARCHGUARD_ANALYSIS_TIMEOUT` | Analysis pipeline timeout (seconds) | `600` |
| `ARCHGUARD_PIP_AUDIT_TIMEOUT` | pip-audit scan timeout (seconds) | `60` |
| `ARCHGUARD_SKIP_ML` | Set to `1` to skip Layer 3 (faster but less thorough) | (unset) |

### LLM / AI

| Variable | Description | Default |
|----------|-------------|---------|
| `GEMINI_API_KEY` | For all AI features (explanations, Advisor, fix suggestions) | (optional) |
| `ARCHGUARD_PRIMARY_MODEL` | Verified at boot and reported by `/api/v1/capabilities`; does **not** select the model for a call — `GEMINI_MODEL` does | `gemini-3.6-flash` |
| `ARCHGUARD_MOCK_LLM` | Set to `1` to mock LLM responses (CI/testing) | (unset) |

### Session

| Variable | Description | Default |
|----------|-------------|---------|
| `ARCHGUARD_SESSION_COOKIE_TTL` | Login session duration (seconds) | `86400` (24h) |

---

## Release Checklist

### Pre-release

- [ ] All tests pass: `pytest tests/unit/ tests/integration/`
- [ ] Coverage ≥ 76%: `pytest --cov=archguard --cov-fail-under=76`
- [ ] Ruff linter passes: `ruff check archguard/`
- [ ] Mypy passes: `mypy archguard/ --ignore-missing-imports`
- [ ] Self-analysis: **no longer available.** It ran through the CLI, which was
      removed, and nothing replaced it. Releases currently ship without a
      self-analysis or fitness gate; the engine is exercised only by the test
      suite's fixtures. Tracked as an open gap rather than quietly dropped.
- [ ] Security scan completes: `pip-audit --format=json && bandit -r archguard/ -ll`
- [ ] Smoke test passes: `BASE_URL=http://localhost:8000 ./scripts/smoke_test.sh`
- [ ] CHANGELOG.md updated with release notes
- [ ] Version bumped in `pyproject.toml`

### Build

- [ ] Docker image builds: `docker build -t archguard:release .`
- [ ] Docker health check passes: `docker run --rm archguard:release python -c "import urllib.request; ..."`
- [ ] Image tagged with version and `latest`

### Deploy

- [ ] `ARCHGUARD_DASHBOARD_TOKEN` set in target environment
- [ ] `ENVIRONMENT=production` set
- [ ] Database/file permissions correct (`/app/.archguard-cache` writable)
- [ ] `ALLOWED_ORIGINS` configured for production frontend domain
- [ ] Reverse proxy (if any): pass `X-Forwarded-For` from trusted proxy IPs
- [ ] Health check configured: `GET /ready` — this is what `render.yaml` and
      `railway.toml` use. `/health` is liveness only and answers 200 whether or
      not the process can reach PostgreSQL or Redis; `/ready` checks them.

### Post-deploy

- [ ] Health endpoint returns `{"status":"ok"}`
- [ ] Smoke tests pass against production URL
- [ ] Logs show no startup errors
- [ ] Dashboard UI loads and shows data

---

## Rollback Procedure

### Docker Compose

```bash
# 1. Revert to previous image tag
docker compose stop app
docker compose rm app
# Edit docker-compose.yml to pin the previous image version
docker compose up -d

# 2. Verify
curl http://localhost:8000/health
```

### Render / Railway

1. In the dashboard, select the previous deployment from the deploy history.
2. Click **Rollback** — the platform re-deploys the previous image.
3. Verify the health endpoint returns `{"status":"ok"}`.

### Data Safety

During rollback:
- **Audit logs** (`/app/.archguard-cache/audit.jsonl`) are persisted on a Docker volume — not affected by rollback.
- **Sessions and rate limits** live in Redis with a TTL, so they survive a
  restart and are shared across replicas. Users are not signed out by a deploy.
- **Analysis workspaces** in `/tmp` are ephemeral; stale ones are swept on the
  next startup and by a periodic sweep.
- **Database migrations are the part that does not roll back.** Users, jobs,
  runs, findings, suppressions and watches are in PostgreSQL under Alembic.
  `docker-entrypoint.sh` runs `alembic upgrade head` before the web process
  starts, so redeploying an older image leaves the newer schema in place.
  Check whether the migrations between the two revisions are backward
  compatible before rolling back, and downgrade deliberately if they are not.

### Rollback Criteria

Roll back immediately if:
- Health check fails after deployment.
- More than 5% of requests return 5xx errors.
- Dashboard UI fails to load or shows blank state.
- Analysis pipeline exits with unexpected errors on the self-analysis CI gate.

---

## Operational Runbook

### Startup

On startup, the application:
1. Logs missing optional env vars as warnings (not errors).
2. Cleans up stale analysis workspaces older than 1 hour.
3. Starts periodic workspace cleanup (every 15 minutes).
4. Logs a multi-instance warning (in-memory state).
5. Begins listening on port 8000.

The `/health` endpoint returns 200 once the application is ready.

### Shutdown

On SIGTERM/SIGINT (e.g., `docker stop`, Render restart):
1. Running analysis jobs are cancelled with a 5-second timeout.
3. Workspaces from cancelled jobs are NOT deleted immediately — the next startup's cleanup sweep removes them.
4. The process exits within 5 seconds.

### Logs

Log format is JSON when output is not a TTY (Docker, CI):

```json
{"timestamp":"2026-07-21T12:00:00+00:00","level":"INFO","logger":"archguard.startup","message":"Dashboard ready.","module":"app","function":"_lifespan","line":92}
```

Key loggers:
- `archguard.startup` — startup/shutdown events
- `archguard.http` — HTTP request logging (with correlation ID)
- `archguard.exceptions` — unhandled exceptions (always at ERROR level)
- `archguard.job_manager` — job lifecycle events

### Health Check

```
GET /health
→ 200 {"status":"ok","version":"1.0.0","environment":"production","uptime_seconds":3600}
```

The health endpoint:
- Always returns 200 when the application is running.
- Does NOT check database connectivity (no database).
- Does NOT check disk space (monitor separately).
- Does NOT check LLM API availability.

### Known Operational Limits

- **In-memory state**: Sessions, rate limits, and active jobs are lost on process restart. Single-instance only. See ADR-008 for the threat model.
- **Analysis timeout**: The `asyncio.wait_for` timeout raises a clean error but the underlying thread continues until completion (see ADR-005).
- **Workspace lifecycle**: Analysis workspaces in `/tmp` are cleaned up within 15 minutes of job completion (or on next startup after a crash).
- **Concurrent analyses**: Capped at 3 concurrent jobs (semaphore in `job_manager.py`).

---

## Monitoring Recommendations

### Health Check

Configure your platform to poll `GET /health` every 30 seconds with a 10-second timeout and 3 retries before marking the instance unhealthy. (This is the default in the Docker HEALTHCHECK instruction.)

### Metrics to Watch

| Metric | Where | Alert Threshold |
|--------|-------|-----------------|
| HTTP 5xx rate | Platform dashboard | >1% over 5 minutes |
| Analysis time | Job duration in logs | >300s for small repos |
| Workspace count | `/tmp` disk usage | >500 MB |
| Session count | In-memory `_SESSIONS` size | >1000 active sessions (rate limit may fail first) |
| LLM API errors | `archguard.llm` logger | >10% error rate over 5 minutes |

### Log-based Alerts

Alert on any log line containing:
- `"exception"` — unhandled exceptions
- `"Unexpected failure"` — analysis pipeline crash
- `"ARCHGUARD_DASHBOARD_TOKEN is not set"` — missing auth config
- `"Cancelled"` — shutdown-time job cancellations (expected during rolling restarts)

---

## Backup and Recovery

### What to Back Up

| Data | Location | Backup Strategy |
|------|----------|-----------------|
| Audit logs | `/app/.archguard-cache/audit.jsonl` | Docker volume — backup nightly |
| Embedding cache | `/app/.archguard-cache/embeddings.db` | Can be regenerated (expensive) |
| Configuration | `.env` file or platform secrets | Backup separately (secret manager) |

### What NOT to Back Up

- `/tmp` workspaces — ephemeral, cleaned up automatically.
- In-memory session store — transient, lost on restart anyway.
- `.archguard.yml` in analyzed repos — these belong to the analyzed projects.

### Recovery Procedure

1. **Full data loss**: Restore the Docker volume from backup, restart the service, re-login.
2. **Corrupted audit log**: Delete `audit.jsonl` — a new one is created on the next analysis run (historical trend data is lost).
3. **Lost `ARCHGUARD_DASHBOARD_TOKEN`**: Generate a new one. User sessions are
   signed with `SESSION_SECRET` and are unaffected; what is invalidated is any
   in-flight SSE stream token, so a browser watching a running analysis falls
   back to polling until the page is reloaded.

---

## Troubleshooting

| Symptom | Likely Cause | Resolution |
|---------|-------------|------------|
| `{"detail":"Invalid or missing token"}` | An `Authorization: Bearer` header that does not match `ARCHGUARD_DASHBOARD_TOKEN` | Check the env var, or drop the header and sign in instead |
| `{"detail":"Not authenticated. Sign in..."}` | No session and no operator credential on a request from a non-loopback address | Sign in through the browser, or send a Bearer header |
| Analysis refuses with "no analysable module" | The repository has no directory the contract generator will treat as a module | Nothing to run: the generator declined deliberately rather than scoring a repository whose contract covers no files |
| Health check fails | Port mismatch or app not started | Check `PORT` env var; verify uvicorn starts |
| `ModuleNotFoundError: No module named 'sentence_transformers'` | ML dependencies not installed | Install with `pip install archguard[ml]` or set `ARCHGUARD_SKIP_ML=1` |
| `pip-audit not found in PATH` | pip-audit not installed | `pip install pip-audit` |
| Dashboard shows the empty state | No analyses for this account yet | Submit a repository at `/`. A *failed* load looks different: it says what went wrong and offers a retry |
| Rate limit errors | Too many requests from one IP | Check `X-RateLimit-Remaining` header; wait before retrying |
| Session expires frequently | `ARCHGUARD_SESSION_COOKIE_TTL` too low | Increase to `86400` (24h) or higher |
