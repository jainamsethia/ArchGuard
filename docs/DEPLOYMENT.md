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
- **ARCHGUARD_DASHBOARD_TOKEN** (required for any Internet-facing deployment)

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

### Option B: Render (Managed Platform)

1. Fork the repository to your GitHub account.
2. In Render Dashboard → New → Web Service → Connect your fork.
3. **Settings:**
   - Build Command: `docker build -t archguard .`
   - Start Command: (uses Dockerfile CMD — uvicorn)
   - Health Check Path: `/health`
4. **Environment Variables (set in Render Dashboard → Secrets):**
   - `ARCHGUARD_DASHBOARD_TOKEN` — **required**
   - `ENVIRONMENT=production`
   - `GEMINI_API_KEY` — optional, for all AI features
   - `GITHUB_TOKEN` — optional, for higher API rate limits
   - `ALLOWED_ORIGINS` — your frontend domain(s)
5. Deploy.

### Option C: Railway

1. Fork the repository.
2. In Railway → New Project → Deploy from GitHub repo.
3. Set `ENVIRONMENT=production` and `ARCHGUARD_DASHBOARD_TOKEN` in the dashboard.
4. Railway sets `PORT=8000` automatically (see `railway.toml`).

### Option D: Bare Metal / VPS

```bash
pip install archguard[dashboard,cloud,ml]
export ARCHGUARD_DASHBOARD_TOKEN=$(python -c "import secrets; print(secrets.token_hex(32))")
export ENVIRONMENT=production
uvicorn archguard.dashboard.app:app --host 0.0.0.0 --port 8000 --workers 2
```

---

## Environment Configuration

### Required

| Variable | Description | Default |
|----------|-------------|---------|
| `ARCHGUARD_DASHBOARD_TOKEN` | Auth token for dashboard API. Generate with `secrets.token_hex(32)` | (none — required for remote access) |

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
| `ARCHGUARD_PRIMARY_MODEL` | Primary LLM model | `claude-sonnet-4-20250514` |
| `ARCHGUARD_MOCK_LLM` | Set to `1` to mock LLM responses (CI/testing) | (unset) |

### Session

| Variable | Description | Default |
|----------|-------------|---------|
| `ARCHGUARD_SESSION_COOKIE_TTL` | Login session duration (seconds) | `86400` (24h) |

---

## Release Checklist

### Pre-release

- [ ] All tests pass: `poetry run pytest tests/unit/ tests/integration/`
- [ ] Coverage gate passes: it is **79%**, enforced by `--cov-fail-under=79` in
      `pyproject.toml`, so `poetry run pytest` applies it without a flag
- [ ] Frontend tests pass: `npm test`
- [ ] Browser and accessibility tests pass: `npx playwright test tests/visual/ tests/a11y/`
- [ ] Ruff linter passes: `poetry run ruff check archguard/ tests/ scripts/`
- [ ] Mypy passes: `poetry run mypy archguard/ --ignore-missing-imports`
- [ ] Alembic round-trips and shows no model drift: `alembic upgrade head`,
      `alembic downgrade base`, `alembic upgrade head`, `alembic check`
- [ ] Security scan completes: `poetry run pip-audit --ignore-vuln PYSEC-2026-196`
      and `poetry run bandit -r archguard/ -ll` (both hard-fail in CI)
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
- [ ] Health check configured: `GET /health`

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
- **In-memory state** (sessions, rate limits, active jobs) is lost on restart — users must re-login.
- **Analysis workspaces** in `/tmp` are ephemeral; stale ones are cleaned up on next startup.
- **No database migrations** — ArchGuard does not use a relational database.

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
3. **Lost `ARCHGUARD_DASHBOARD_TOKEN`**: Generate a new one (all existing sessions are invalidated — users must re-login).

---

## Troubleshooting

| Symptom | Likely Cause | Resolution |
|---------|-------------|------------|
| `401 Sign in with GitHub to continue.` | No session cookie, and the local-development fallback does not apply | Sign in through the UI. In production the fallback is off by design, so `GITHUB_OAUTH_CLIENT_ID` / `_SECRET` must be set |
| `401 Invalid or missing token` | `ARCHGUARD_DASHBOARD_TOKEN` is set, but the request carried no matching `Authorization: Bearer` header and no valid session | Send the operator token as a Bearer header, or sign in for a session cookie. This is the API and CLI path, not the browser one |
| `401 Dashboard requires ARCHGUARD_DASHBOARD_TOKEN to be set for remote access` | No token configured and the caller is not on localhost | Set `ARCHGUARD_DASHBOARD_TOKEN`. `ARCHGUARD_DASHBOARD_ALLOW_REMOTE=1` also silences it, by disabling the check -- do not use it on an Internet-facing deployment |
| A run reports guessed module boundaries | The analysed repository has no `.archguard.yml` | Expected: one is generated headlessly. The report says whether boundaries were measured from co-change history or inferred from directory names |
| Health check fails | Port mismatch or app not started | Check `PORT` env var; verify uvicorn starts |
| `ModuleNotFoundError: No module named 'sentence_transformers'` | The worker extra is not installed | `poetry install --extras worker`, or set `ARCHGUARD_SKIP_ML=1` to run layers 1 and 2 only. Layers 3 and 4 then report as unmeasured rather than as zero |
| `pip-audit not found in PATH` | pip-audit not installed | `pip install pip-audit` |
| Dashboard shows empty data | No analyses have been run yet | Submit a repository URL on the landing page. The empty state links back to it |
| Rate limit errors | Too many requests from one IP | Check `X-RateLimit-Remaining` header; wait before retrying |
| Session expires frequently | `ARCHGUARD_SESSION_COOKIE_TTL` too low | Increase to `86400` (24h) or higher |
