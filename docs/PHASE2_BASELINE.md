# Phase 2 — Deployment Baseline Audit

**Date:** 2026-06-23  
**Auditor:** Automated (Antigravity)  
**Scope:** Install, dashboard startup, env-var inventory, Docker build, docker-compose, frontend load

---

## Step A — Install Result

**Status: ✅ PASS**

```
poetry install --with dev
Installing dependencies from lock file
No dependencies to install or update
Installing the current project: archguard (0.3.0)
Exit code: 0
```

- All dependencies were already resolved from the lock file.
- No warnings or errors.
- Dev group installed cleanly.

> **Note:** `fastapi`, `uvicorn`, and `cachetools` are declared as *optional* extras
> (`[dashboard]`), but `fastapi >=0.100.0` is also in `[tool.poetry.group.dev.dependencies]`.
> The dashboard starts only because the dev group provides FastAPI; a production
> `poetry install` (without `--with dev` or `--extras dashboard`) **will not install
> FastAPI/uvicorn**, causing `ImportError` at runtime.

---

## Step B — Dashboard Startup Result

### Via `uvicorn` directly

**Status: ✅ STARTS CLEANLY**

```
poetry run uvicorn archguard.dashboard.app:app --port 8000
INFO:     Started server process [20768]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

No crash, no errors.

### Via CLI `python -m archguard dashboard`

**Status: ✅ STARTS CLEANLY**

```
poetry run python -m archguard dashboard
Starting ArchGuard dashboard at http://127.0.0.1:8080
Dashboard running. Set ARCHGUARD_DASHBOARD_TOKEN to secure remote access.
INFO:     Started server process [12976]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8080 (Press CTRL+C to quit)
```

- CLI defaults to port **8080** (not 8000); the Dockerfile ENTRYPOINT runs `python -m archguard` which starts the CLI.
- **The Dockerfile does NOT expose a port nor start uvicorn directly.** The ENTRYPOINT drops the user into the CLI, not the dashboard.
- To run the dashboard in Docker, the user would need to override the entrypoint or invoke `archguard dashboard --host 0.0.0.0 --port 8000`.

---

## Step C — Environment Variable Inventory

### Variables referenced in source code

| Variable | File(s) | Default | Required? | In `.env.example`? |
|---|---|---|---|---|
| `ANTHROPIC_API_KEY` | `contract/llm_inference.py`, `llm/cloud.py`, `llm/advisor.py` | `""` (empty) | Optional (skip LLM if absent) | ✅ Yes |
| `ARCHGUARD_PRIMARY_MODEL` | `contract/llm_inference.py`, `llm/cloud.py` | `claude-sonnet-4-20250514` | Optional | ✅ Yes |
| `ARCHGUARD_FALLBACK_MODEL` | `llm/cloud.py` | `claude-haiku-4-5-20251001` | Optional | ✅ Yes |
| `ANTHROPIC_MODEL` | `llm/advisor.py` | `claude-sonnet-4-20250514` | Optional | ✅ Yes |
| `OPENAI_API_KEY` | `llm/openai_provider.py`, `llm/remediation.py` | `""` (empty) | Optional (advisor panel) | ✅ Yes |
| `OPENAI_MODEL` | `llm/openai_provider.py`, `llm/remediation.py` | `gpt-4-turbo` | Optional | ✅ Yes |
| `OPENAI_BASE_URL` | `llm/openai_provider.py`, `llm/remediation.py` | `https://api.openai.com/v1` | Optional | ✅ Yes |
| `OLLAMA_MODEL` | `llm/local.py` | `llama3` | Optional | ✅ Yes |
| `OLLAMA_BASE_URL` | `llm/local.py` | `http://localhost:11434` | Optional | ❌ **Missing** |
| `ARCHGUARD_DASHBOARD_TOKEN` | `dashboard/_state.py` | None (no auth) | Optional | ✅ Yes |
| `ARCHGUARD_DASHBOARD_ALLOW_REMOTE` | `dashboard/_state.py`, `cli/dashboard_cmd.py` | `""` (deny) | Optional | ✅ Yes |
| `ARCHGUARD_SESSION_TTL` | `dashboard/_state.py` | `3600` | Optional | ✅ Yes |
| `ARCHGUARD_AUDIT_SECRET` | `audit/logger.py` | None | Optional | ✅ Yes |
| `ARCHGUARD_AUDIT_STRICT` | `audit/logger.py` | `""` (off) | Optional | ✅ Yes |
| `ARCHGUARD_AUDIT_MAX_ENTRIES` | `config.py` | `1000` | Optional | ✅ Yes |
| `ARCHGUARD_AUDIT_MAX_SIZE_MB` | `config.py` | `10` | Optional | ✅ Yes |
| `ARCHGUARD_EMBEDDING_BATCH_SIZE` | `config.py` | `500` | Optional | ✅ Yes |
| `ARCHGUARD_LOCK_TIMEOUT` | `cache/locking.py` | `30.0` | Optional | ✅ Yes |
| `ARCHGUARD_SKIP_ML` | `analysis/_orchestrator_layer4.py`, `analysis/_orchestrator_stages.py` | `""` (off) | Optional | ✅ Yes |
| `ARCHGUARD_SKIP_LLM` | `cli/_analyze_core.py` | `""` (off) | Optional | ✅ Yes |
| `ARCHGUARD_MOCK_LLM` | `llm/cloud.py` | None | Optional | ✅ Yes |
| `ARCHGUARD_SLACK_WEBHOOK` | `cli/_analyze_output.py` | None | Optional | ✅ Yes |
| `GITHUB_TOKEN` | `cli/_analyze_github.py`, `github/client.py` | None | Optional (CI) | ✅ Yes |
| `ARCHGUARD_S3_BUCKET` | `cli/sync_cmd.py` (via Typer envvar) | **(none — required if sync used)** | Conditional | ❌ **Missing** |
| `AWS_PROFILE` | `cli/sync_cmd.py` (via Typer envvar) | None | Optional | ❌ **Missing** |
| `AWS_ACCESS_KEY_ID` | `.env.example` only | None | Optional (S3) | ✅ Yes |
| `AWS_SECRET_ACCESS_KEY` | `.env.example` only | None | Optional (S3) | ✅ Yes |

### GitHub-Actions-only variables (auto-set by CI, not user-configured)

| Variable | File(s) | Purpose |
|---|---|---|
| `GITHUB_EVENT_PATH` | `cli/github_sync_cmd.py`, `github/client.py` | CI event payload path |
| `GITHUB_REPOSITORY` | `cli/github_sync_cmd.py`, `cli/_analyze_core.py` | `owner/repo` slug |
| `GITHUB_SHA` | `cli/_analyze_github.py` | Head commit SHA |
| `GITHUB_ACTIONS` | `cli/_init_dispatch.py`, `utils/tty.py` | CI detection flag |
| `CI` | `utils/tty.py` | Generic CI detection |
| `SystemDrive` | `utils/validation.py` | Windows-only path root |

### Gaps in `.env.example`

| Missing from `.env.example` | Source Location |
|---|---|
| `OLLAMA_BASE_URL` | `llm/local.py:56` |
| `ARCHGUARD_S3_BUCKET` | `cli/sync_cmd.py:18` |
| `AWS_PROFILE` | `cli/sync_cmd.py:24` |

---

## Step D — Docker Build Result

**Status: ❌ FAIL — Docker Desktop not running**

```
docker build -t archguard-test . 2>&1
ERROR: error during connect: Head "http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/_ping":
open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.
Build exit code: 1
```

**Root cause:** Docker Desktop (Linux engine) is not running on this Windows machine.

### Dockerfile analysis (static review)

The Dockerfile is a valid multi-stage build, but has the following deployment concerns:

1. **ENTRYPOINT starts the CLI, not the dashboard:**
   ```dockerfile
   ENTRYPOINT ["python", "-m", "archguard"]
   ```
   This drops into the Typer CLI, not uvicorn. To serve the dashboard the user must run:
   ```
   docker run archguard-test dashboard --host 0.0.0.0 --port 8000
   ```

2. **No `EXPOSE` directive** — no port is documented in the Dockerfile.

3. **No health check** — no `HEALTHCHECK` instruction.

4. **Optional extras not installed:** `poetry export` without `--extras dashboard` means
   `fastapi`, `uvicorn`, and `cachetools` are **not** in the Docker image. The dashboard
   will fail with `ImportError` inside the container.

5. **Poetry version pinned to 2.4.1** — acceptable but worth documenting.

---

## Step E — docker-compose

**Status: ❌ NO docker-compose.yml found**

```
Test-Path docker-compose.yml  → False
Test-Path docker-compose.yaml → False
```

No compose file exists anywhere in the repository.

---

## Step F — Frontend Loads

**Status: ✅ YES — HTTP 200**

```
Invoke-WebRequest http://localhost:8000/
StatusCode: 200
Content-Length: 47499 bytes
Content-Type: text/html (index.html served via StaticFiles)
```

- Single-page application served from `archguard/dashboard/static/index.html` (47.5 KB).
- Vendor assets in `archguard/dashboard/static/vendor/`.
- Static files mounted at `/` via FastAPI `StaticFiles(html=True)`.

---

## Step G — First Failure Points Summary

| Check | First Failure Point |
|---|---|
| Install (`poetry install --with dev`) | No failure — passes cleanly |
| Dashboard startup (uvicorn direct) | No failure — starts on port 8000 |
| Dashboard startup (CLI `archguard dashboard`) | No failure — starts on port 8080 |
| Env vars | 3 vars in source missing from `.env.example` |
| Docker build | Docker Desktop not running (cannot test) |
| docker-compose | File does not exist |
| Frontend load (curl) | No failure — 200 OK, 47.5 KB HTML |

---

## Summary: Gaps to Fix Before Phase 2 Can Deploy

### 🔴 Critical (blocks deployment)

1. **Dashboard extras not in Docker image.** The Dockerfile's `poetry export` does not
   include `--extras dashboard`, so `fastapi`, `uvicorn`, and `cachetools` are omitted
   from the production image. The dashboard **will fail with `ImportError`** inside
   Docker.

2. **No `docker-compose.yml`.** There is no compose file for orchestrating the service,
   port mapping, environment variable injection, volume mounts, or health checks.

3. **Dockerfile ENTRYPOINT runs CLI, not the server.** `ENTRYPOINT ["python", "-m", "archguard"]`
   starts the Typer CLI. To deploy the dashboard, either:
   - Change the entrypoint to run uvicorn, or
   - Add a `CMD ["dashboard", "--host", "0.0.0.0", "--port", "8000"]` override.

4. **No `EXPOSE` in Dockerfile.** Port is not documented for container orchestration.

### 🟡 Important (should fix)

5. **3 env vars missing from `.env.example`:**
   - `OLLAMA_BASE_URL` (local LLM base URL)
   - `ARCHGUARD_S3_BUCKET` (S3 cache sync)
   - `AWS_PROFILE` (S3 cache sync)

6. **No `HEALTHCHECK` in Dockerfile.** Container orchestrators (Docker, K8s) cannot
   verify the service is actually responding.

7. **Production install path:** `poetry install` without `--with dev` and without
   `--extras dashboard` will not install FastAPI. The production install command must
   be `poetry install --extras dashboard` (or `--extras all`).

### 🟢 Informational

8. **CLI dashboard defaults to port 8080**, while `uvicorn` direct invocation used port
   8000 in the audit. These should be consistent or clearly documented.

9. **Docker build could not be tested** because Docker Desktop was not running. The
   Dockerfile should be validated in CI.

10. **ArchGuard does not auto-load `.env` files** (documented in `.env.example` header).
    Users must manually export variables or use direnv/python-dotenv.

11. **README does not document how to start the live dashboard.** The README describes
    `archguard report --output dashboard.html --open` (static HTML report), but the
    `archguard dashboard` CLI command that starts the FastAPI server is not mentioned.
    A Railway demo link exists (`https://archguard-demo.up.railway.app`) but no local
    startup instructions.

12. **Phantom env vars in README:** `ARCHGUARD_TEST_MODE` and `ARCHGUARD_LLM_PROVIDER`
    are mentioned in the README but have **no effect** — neither is read anywhere in
    source code.
