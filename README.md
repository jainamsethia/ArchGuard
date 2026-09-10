# ArchGuard

**Architectural drift detection for Python repositories, as a web application.**

[![CI](https://github.com/jainamsethia/ArchGuard/actions/workflows/ci.yml/badge.svg)](https://github.com/jainamsethia/ArchGuard/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

Paste a public Python repository on GitHub. ArchGuard clones it, reads its
import graph, and reports an architectural health score, the modules carrying
the most risk, and what it found — with a sentence of plain English for every
finding.

The thing it refuses to do is guess. When a layer cannot run — no import rules
declared, no baseline to compare against, ML extras absent — the report says so
and that layer is excluded from the composite, rather than scored zero and
folded into your grade. A missing measurement and a clean one are not the same
thing, and this codebase is built around not conflating them.

---

## The problem

Architecture decays in increments that no individual code review catches. A
module starts importing something it was meant not to know about. Two services
grow a third copy of the same logic. A package that was cohesive drifts into a
grab-bag. Every one of those changes is defensible on its own; the aggregate is
a codebase nobody wants to touch.

Linters do not see this — they operate on files, and architecture is a property
of the *relationships between* files. The signal only exists at the level of the
import graph, the module boundaries, and how both change over time.

ArchGuard measures that, gives it a number, and tracks the number across commits.

---

## Key features

| Feature | What it actually gives you |
|---|---|
| **Four-layer analysis** | Import-boundary violations, coupling deltas, semantic drift and cross-module duplication — each scored separately, so a single bad number is traceable to a cause |
| **Honest skip reporting** | Any layer that could not measure reports *why*, and is excluded from the composite rather than scored 0.00 |
| **Architecture fitness functions** | Contract-declared assertions (`graph.cycles == 0`, `layer[2].debt <= 0.60`, `health_score >= 80`) evaluated per run, with `critical` gates capping the grade |
| **Auto-generated contracts** | Repositories with no `.archguard.yml` get one synthesised from their actual package layout, so first-time analysis works without setup |
| **Incremental re-analysis** | File-hash cache in PostgreSQL; unchanged files are skipped, so a re-scan does materially less work than the first |
| **Dependency scanning** | `pip-audit` against pinned requirement files, surfacing CVEs per package |
| **Architecture evolution** | Commit-history analysis via PyDriller, with Louvain community detection to show how module clustering shifted |
| **Watched repositories** | Scheduled re-scans with webhook alerts when health regresses past a threshold |
| **AI Advisor & remediation plans** | Optional Gemini-backed explanation and fix-planning over the findings — degrades cleanly to disabled controls without a key |
| **Suppressions** | Per-user, per-finding suppressions stored in PostgreSQL, so a known-and-accepted violation stops re-appearing |

---

## How it works

```
GitHub URL → validate → clone → parse → 4 layers → score → persist → dashboard
```

1. **Validate.** The URL is parsed and resolved. The IP it resolves to is checked
   against private ranges, and the outbound request is pinned to that address.
2. **Queue.** The web process enqueues a job in Redis and returns a job id. It
   does no analysis itself.
3. **Clone.** A worker clones the repository into a temporary workspace under a
   size budget.
4. **Contract.** `.archguard.yml` is loaded, or synthesised from the repository's
   package structure if absent.
5. **Analyse.** Four layers run over the changed files (or all of them on a first
   scan). Each returns a score, its violations, and a skip reason if it measured
   nothing.
6. **Score.** Layer scores combine into a composite ArchDebt value; fitness
   functions are evaluated; a health score and grade fall out.
7. **Persist.** Run, violations and file hashes are written to PostgreSQL.
8. **Stream.** Progress is pushed to the browser over Server-Sent Events while
   the job runs; the dashboard renders the completed run.

### The four layers

| Layer | Measures | Method | Needs ML extras |
|---|---|---|---|
| **1 — Import Boundary Violations** | Imports that breach declared module rules | AST import extraction, resolved against contract `allowed`/`disallowed` | No |
| **2 — Coupling Delta** | Modules growing more entangled than their budget | Import-graph edge counting per module | No |
| **3 — Semantic Drift** | A module's code drifting from what it used to be about | `all-MiniLM-L6-v2` embeddings, centroid cosine distance against a stored baseline | **Yes** |
| **4 — Duplication** | Near-identical logic across module boundaries | FAISS `IndexFlatL2` similarity search over 384-dim embeddings | **Yes** |

Layers 3 and 4 require the `worker` extra (torch, sentence-transformers,
faiss-cpu). Without it they skip with an explicit reason and are excluded from
the composite — the score you get is over the layers that actually ran.

### Scoring

Composite ArchDebt is the weighted mean of the layers that ran (equal weights by
default, `0.25` each), on a 0–1 scale where **higher is worse**. Health score is
its inverse: `(1 − composite) × 100`.

Bands are set relative to the contract's own thresholds, not hardcoded:

| Band | Condition |
|---|---|
| `Healthy` | composite < `warn_threshold / 2` |
| `Watch` | `warn_threshold / 2` ≤ composite < `warn_threshold` |
| `Critical` | composite ≥ `fail_threshold` |

A failing `critical` fitness function caps the grade at **C** regardless of the
numeric score — a repository with cycles in its module graph does not get an A.

---

## Architecture

Two processes, one codebase, two different container images. The split is
deliberate: the web process never loads an embedding model, and torch alone
outweighs everything else combined.

```mermaid
flowchart TD
    U["Browser"]

    subgraph web["Web image (slim)"]
        W["FastAPI<br/>Jinja2 + ES modules"]
    end

    subgraph wrk["Worker image (torch + faiss + baked MiniLM)"]
        K["arq worker"]
        L1["L1 Import boundaries"]
        L2["L2 Coupling"]
        L3["L3 Semantic drift"]
        L4["L4 Duplication"]
    end

    R[("Redis<br/>queue · sessions · rate limits")]
    P[("PostgreSQL")]
    G["GitHub"]
    AI["Gemini API<br/>advisor · remediation"]

    U -->|"GitHub OAuth"| W
    W -->|"enqueue job"| R
    R -->|"consume"| K
    K --> L1 --> L2 --> L3 --> L4
    K -->|"clone"| G
    K -->|"progress"| R
    L4 -->|"run · violations · file hashes"| P
    W -->|"read"| P
    R -->|"SSE"| W
    W -.->|"optional"| AI
```

**Why a queue at all.** Analysis clones and parses an arbitrary repository from
the internet. Doing that in the process that holds every session key is a bad
trade, and a 15-minute analysis inside a request is not a request. The worker
also owns the crons: watched-repository sweeps and history retention.

**Why two images.** The web service must not be able to `import torch`, and the
worker must have `pip-audit` on its PATH. Both are asserted in CI on every push,
because both regress silently — an extra in the wrong list looks like nothing
until the image is built.

---

## Technical deep dive

**Semantic drift needs a baseline, and says so when it has none.** Layer 3
compares a module's current embedding centroid against a stored one. On a first
scan there is nothing to compare to, so it returns `skipped` with that reason
rather than a drift of 0.0 — which would read as "measured, no drift" and
average into the composite as a real, perfect measurement.

**Incremental analysis must not change the answer.** File hashes are persisted
per repository, and unchanged files are skipped on re-scan. Layer 4 is the
exception: duplication is a repository-wide property, so any module change
recomputes it across the whole tree. An incremental scan of a given tree state
is required to report the same layer set as a full scan of it — anything else
means two different scores for one repository, which is the one thing
incremental analysis is not allowed to do.

**SSRF protection pins the address, not the name.** Validating a hostname and
then letting the HTTP client re-resolve it leaves a DNS-rebinding window. The
validator resolves the host, rejects private and loopback ranges, then issues the
request against the resolved IP with `sni_hostname` set — so TLS is still
verified against the real hostname while the connection cannot be re-pointed.

**The dependency scanner does not build what it scans.** `pip-audit` resolving a
`pyproject.toml` executes the project's own PEP 517 backend — arbitrary code from
a repository submitted by a stranger. Only already-pinned requirement files are
passed (`pip-audit -r`); a bare `pyproject.toml` is skipped with an explicit
reason, and tests assert `subprocess.run` is never reached on that path.

**ArchGuard analyses itself in CI.** `python -m archguard.release_gate` runs the
orchestrator against this repository's own `.archguard.yml` and exits non-zero if
it breaches its own thresholds or a `critical` fitness gate. It calls the
orchestrator directly — no HTTP, no queue, no database — so a queue outage cannot
masquerade as an architectural regression.

---

## Project structure

```
archguard/
├── analysis/          # The four layers, scoring, orchestration, ranking
├── contract/          # .archguard.yml loading, validation, auto-generation
├── dashboard/         # FastAPI app, routes, templates, static frontend
│   ├── routes/        # jobs, runs, evolution, advisor, watch, auth, meta…
│   ├── static/js/     # ES modules — no build step
│   └── templates/     # Jinja2
├── db/                # SQLAlchemy models, store, Alembic migrations
├── worker/            # arq worker, tasks, crons, retention
├── llm/               # Gemini client, advisor, remediation prompts
├── evolution/         # Commit-history analysis (PyDriller, Louvain)
├── fitness/           # Fitness-function evaluator
├── watch/ alerting/   # Scheduled re-scans, webhook delivery
├── cache/             # Incremental file-hash + embedding cache
├── utils/             # URL validation, secret redaction, paths
└── release_gate.py    # ArchGuard checking ArchGuard

tests/                 # unit · integration · frontend · e2e · a11y · visual
docs/                  # DEVELOPMENT.md, DEPLOYMENT.md, ADRs
```

---

## Getting started

### Prerequisites

- **Python 3.11+** and [Poetry](https://python-poetry.org/)
- **PostgreSQL 14+** and **Redis 6+** — nothing falls back to a file store
- **Docker** (optional, for the compose path)
- **Node 22.12+** (optional, only for the frontend/browser test suites)

### Install

```bash
git clone https://github.com/jainamsethia/ArchGuard.git
cd ArchGuard
poetry install --with dev
```

Layers 3 and 4 additionally need the ML extras:

```bash
poetry install --with dev --extras worker
```

### Configure

```bash
cp .env.example .env
```

`.env` must define at minimum:

```
DATABASE_URL=postgresql+asyncpg://archguard:archguard_local_dev@127.0.0.1:5432/archguard_dev
TEST_DATABASE_URL=postgresql+asyncpg://archguard:archguard_local_dev@127.0.0.1:5432/archguard_test
REDIS_URL=redis://127.0.0.1:6379/0
```

> **Use `127.0.0.1`, not `localhost`.** Where `localhost` resolves to `::1` first
> without anything listening on IPv6, every connection waits out the failed
> attempt — measured at 2.13s versus 0.09s per connect.

> **`.env` is read by the application and nothing else.** `load_dotenv()` runs in
> `archguard/dashboard/app.py`. pytest, alembic and the service scripts read the
> process environment, so export it first: `set -a; . ./.env; set +a`

### Run

Start PostgreSQL and Redis:

```bash
docker compose up -d postgres redis
```

Create the test database and bring the schema to head:

```bash
docker compose exec postgres createdb -U archguard archguard_test
```
```bash
poetry run alembic upgrade head
```

Then the two processes, in separate terminals:

```bash
poetry run arq archguard.worker.main.WorkerSettings
```
```bash
make dev
```

The dashboard is at **http://localhost:8000**.

Without a worker running, jobs are accepted and queued but never analysed — the
queue simply grows, with no error to see. `docker compose up` starts both.

---

## Environment variables

Full documentation lives in [`.env.example`](.env.example). The ones that matter:

### Required in production

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection (`postgresql+asyncpg://…`) |
| `REDIS_URL` | Queue, sessions, rate-limit counters |
| `SESSION_SECRET` | HMAC key for session cookies — `secrets.token_hex(32)` |
| `GITHUB_OAUTH_CLIENT_ID` / `GITHUB_OAUTH_CLIENT_SECRET` | Sign-in; without them nobody can authenticate |
| `ALLOWED_ORIGINS` | Exact origins, comma-separated. `*` with credentials is refused |
| `ARCHGUARD_TRUSTED_PROXY_IPS` | Proxy CIDR, or `*` if the platform is the only ingress |
| `ENVIRONMENT` | `production` activates the startup configuration gate |

A production instance **refuses to start** if any of these is missing or unsafe.
Each one it catches is a misconfiguration that otherwise produces no error at
all — only quietly weaker behaviour.

### Optional

| Variable | Default | Effect |
|---|---|---|
| `GEMINI_API_KEY` | unset | Enables AI Advisor and remediation plans; without it those controls report unavailable and disable themselves |
| `GITHUB_TOKEN` | unset | Raises the GitHub API limit above 60 req/hr |
| `ARCHGUARD_DASHBOARD_TOKEN` | unset | Operator credential for reaching the API without a browser. Signed-in users are unaffected by its absence |
| `ARCHGUARD_WORKER_CONCURRENCY` | `2` | Worker `max_jobs` — a memory budget; scale out with processes |
| `ARCHGUARD_JOB_TIMEOUT` | `900` | Seconds before an analysis is abandoned |
| `ARCHGUARD_RETENTION_DAYS` | `90` | How long completed runs are kept |
| `ARCHGUARD_RATE_LIMIT_MAX_REQUESTS` | `50` | Requests per minute per IP (LLM routes: `30`) |
| `ARCHGUARD_PIP_AUDIT_TIMEOUT` | `60` | Raise for large dependency trees |
| `ARCHGUARD_AUDIT_SECRET` | generated | HMAC key for the audit log. **This — not `SESSION_SECRET` — is what signs it** |
| `SENTRY_DSN` | unset | Error reporting. `sentry-sdk` is not a dependency; without it this warns and no-ops |

### Development-only

`ARCHGUARD_SKIP_ML=1` skips layers 3 and 4. `ARCHGUARD_MOCK_LLM=1` serves canned
AI responses so the browser suites can drive those features without a key.
`ARCHGUARD_DASHBOARD_ALLOW_REMOTE` disables the IP guard and is refused by the
production gate.

Never commit `.env` — it is gitignored, and a `DATABASE_URL` carries a password.

---

## Usage

1. **Sign in with GitHub.** Locally, with no OAuth app configured and the request
   coming from loopback, ArchGuard falls back to a `local-dev` account so the
   product is usable without setup. That fallback is impossible in production —
   the config gate refuses to start without OAuth.
2. **Submit a public repository:**
   ```
   https://github.com/owner/repo
   ```
3. **Watch it run.** Progress streams over SSE: cloning → layer 1 → … → complete.
4. **Read the report.** Health score and band, per-layer breakdown, ranked
   violations each with a plain-English explanation, module map, dependency
   scan, and the fitness gates that passed or failed.
5. **Follow it over time.** Re-analyse to get trends and run comparison; add a
   watch to have it re-scanned on a schedule with a webhook on regression.

`/example` serves a stored report of ArchGuard analysing its own repository.

---

## API reference

All endpoints are under `/api/v1` and require an authenticated session (or the
operator token). Rate-limited per IP.

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/jobs` | Submit a repository. Body: `{"github_url": "..."}`. Returns `202` with `job_id`, `poll_url`, `stream_url` |
| `POST` | `/jobs/validate` | Validate a URL and return repository metadata without queueing |
| `GET` | `/jobs/{job_id}` | Job status, progress lines and result |
| `GET` | `/jobs/{job_id}/stream` | SSE progress stream |
| `GET` | `/runs` · `/runs/latest` | Completed runs for the signed-in user |
| `GET` | `/modules` · `/trends/{module}` | Module scores and per-module history |
| `GET` | `/deps?job_id=…` | Dependency scan result |
| `GET` | `/risk` | Ranked findings |
| `POST` | `/evolution/analyze` · `GET` `/evolution/{history,trends,summary,latest}` | Commit-history analysis |
| `POST` | `/advisor/ask` · `GET`/`POST` `/remediation/plan` | AI features (require `GEMINI_API_KEY`) |
| `GET`/`POST`/`PATCH`/`DELETE` | `/watch` | Watched repositories |
| `GET`/`POST`/`DELETE` | `/suppressions` | Suppress or restore findings |
| `GET` | `/capabilities` | Which optional features are available, and why not if they aren't |

Unauthenticated operational endpoints: `/health` (liveness — never fails while
the process runs), `/ready` (readiness — 503 naming the failed dependency), and
`/metrics` (Prometheus text).

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{"github_url": "https://github.com/pallets/itsdangerous"}'
```

> There is **no CLI**. ArchGuard declares no console entry points; the earlier
> CLI was removed and the web application replaced it.

---

## Testing

```bash
set -a; . ./.env; set +a          # the suites read the process environment
```

| Command | Scope |
|---|---|
| `make test` | Unit tests |
| `make test-full` | Unit + integration with coverage |
| `poetry run pytest tests/unit tests/integration -m "not slow"` | What CI's matrix runs |
| `npm test` | Frontend (jsdom, `node --test`) |
| `npx playwright test tests/a11y/` | axe-core accessibility |
| `npx playwright test tests/e2e/` | Browser journeys |
| `npx playwright test tests/visual/` | Visual regression + snapshots |
| `make lint` / `make typecheck` | ruff / mypy |

Integration tests need real PostgreSQL and Redis — they migrate a **separate**
`archguard_test` database up and back down, which is why it must not be your
development database.

Coverage is enforced at **≥79%** (`--cov-fail-under=79` in `pyproject.toml`).
Layers 3 and 4 only execute where the ML extras are installed; CI runs them in a
dedicated job that asserts no test skipped for want of them.

---

## Security

| Measure | Implementation |
|---|---|
| **SSRF protection** | Hostname resolved, private/loopback ranges rejected, request pinned to the resolved IP with `sni_hostname` so TLS still verifies |
| **No build execution** | Dependency scanning passes only pinned requirement files to `pip-audit`; a bare `pyproject.toml` is skipped |
| **CSP with per-request nonce** | `script-src 'self' 'nonce-…'`, plus `object-src 'none'`, `base-uri 'self'`, `frame-ancestors 'none'`, `form-action 'self'` |
| **Security headers** | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy` |
| **Session integrity** | HMAC-signed cookies keyed on `SESSION_SECRET`, stored in Redis with a TTL, `httponly` + `samesite=lax`, `secure` in production |
| **Tenancy** | Every data read is scoped to the signed-in user; the operator token authenticates but deliberately identifies nobody, so it cannot read another account's rows |
| **Rate limiting** | Redis-backed, per real client IP, with a lower ceiling on billable LLM routes |
| **Path traversal** | Job ids and repository paths validated before any filesystem access |
| **Webhook egress** | Alert targets run through the same SSRF validation as inbound URLs, with a capped response read |
| **Secret redaction** | Known credential variable names are filtered out of logs and AI prompts |
| **Startup gate** | A production instance refuses to boot on a misconfiguration that would silently weaken any of the above |
| **Dependency auditing** | `pip-audit` and `bandit` run in CI with no `|| true` — a new advisory fails the build |

Vulnerability reporting: [`SECURITY.md`](SECURITY.md).

---

## CI/CD

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs ten jobs on every
push and pull request:

| Job | Validates |
|---|---|
| Lint & Type Check | ruff, mypy, shellcheck |
| Security Scan | `pip-audit` (hard fail), `bandit -ll` |
| Unit & Integration Tests | Python 3.11 **and** 3.12, against real PostgreSQL and Redis services |
| Layer 3 & 4 (worker extras) | The ML layers, plus an assertion that no test skipped for want of them, plus the release gate |
| Alembic round-trip | `upgrade → downgrade → upgrade`, then `alembic check` for model drift |
| Frontend (jsdom) | `node --test` |
| Docker (web / worker split) | Both images build; web has **no** torch, worker **has** `pip-audit` and loads the model offline; smoke test against the running container |
| Visual regression | Browser behaviour tests |
| Visual snapshots | Pixel comparison against committed Linux baselines (advisory) |
| Browser journeys | axe-core accessibility and end-to-end flows |

[`visual-baselines.yml`](.github/workflows/visual-baselines.yml) regenerates the
Linux snapshot baselines on a runner matching the job that consumes them —
baselines are platform-specific and cannot be produced from a developer machine.

---

## Deployment

Supported through committed manifests. Both targets run **two services** from one
repository — a web service and a worker — plus PostgreSQL and Redis.

| Target | Manifest |
|---|---|
| **Render** | [`render.yaml`](render.yaml) — a Blueprint declaring web, worker, Postgres and Key Value |
| **Railway** | [`railway.toml`](railway.toml) + [`railway.worker.toml`](railway.worker.toml) — one file per service |
| **Docker Compose** | [`docker-compose.yml`](docker-compose.yml) |
| **Bare metal** | uvicorn + arq on separate hosts |

Production Redis must persist and use `noeviction`: it holds the job queue and
every session, neither of which is regenerable. A non-persistent instance drops
queued analyses and signs out every user on restart.

The worker image is selected by the `ARCHGUARD_IMAGE=worker` build argument
rather than a build stage, because not every platform can name one.

[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) has the full procedure, including the
production configuration gate and what each variable does.

> **Status:** the manifests are committed, CI builds and smoke-tests both images
> on every push, and the configuration gate is verified against each manifest's
> declared environment. No public hosted instance is currently running.

---

## Limitations

- **Python only.** The import parser is Python-specific. No other language is
  analysed.
- **Public repositories only.** No GitHub App installation or private-repo access.
- **Semantic drift needs a baseline that survives the clone.** Dashboard runs
  clone fresh each time, so Layer 3 reports "no prior baseline" on repositories
  analysed that way. It is honest about it rather than reporting a false 0.00.
- **Layers 3 and 4 require the ML extras.** Without torch and faiss they skip;
  the composite is then computed over layers 1 and 2 only.
- **Dependency scanning needs pinned requirements.** A repository shipping only
  `pyproject.toml` is skipped by design — see Security.
- **The audit log is write-only.** It is currently written by one code path and
  read by nothing; all dashboard reads are PostgreSQL queries.
- **No screenshots in this README.** The repository holds no dedicated UI
  screenshots; `docs/architecture.png` is stale and deliberately unreferenced.
  Screenshots would belong in `docs/`.

---

## Roadmap

**Implemented** — everything in Key Features above.

**Planned**
- Publish UI screenshots and replace the stale architecture image
- Persist Layer 3 baselines across clones so semantic drift works on dashboard runs
- Make the audit log a real, readable trail in PostgreSQL rather than a write-only file

**Potential**
- Languages beyond Python
- Private repositories via a GitHub App installation
- A hosted public instance

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). In short: [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)
for setup, tests green before a PR, and `make lint typecheck` clean.
Architectural decisions are recorded as ADRs in [`docs/adr/`](docs/adr).

ArchGuard is subject to its own contract — [`.archguard.yml`](.archguard.yml)
declares the module boundaries and fitness gates it must satisfy, and
`python -m archguard.release_gate` enforces them in CI.

---

## License

[MIT](LICENSE) © Jainam Sethia
