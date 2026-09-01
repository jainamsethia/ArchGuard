# ArchGuard
> **Architectural drift detection for Python CI pipelines**
> Catches import boundary violations, coupling degradation, and semantic drift before they reach main.

[![CI](https://github.com/jainamsethia/ArchGuard/actions/workflows/ci.yml/badge.svg)](https://github.com/jainamsethia/ArchGuard/actions/workflows/ci.yml) [![PyPI](https://img.shields.io/pypi/v/archguard)](https://pypi.org/project/archguard/) [![Python](https://img.shields.io/pypi/pyversions/archguard)](https://pypi.org/project/archguard/) [![License](https://img.shields.io/github/license/jainamsethia/ArchGuard)](LICENSE) [![Docker](https://img.shields.io/badge/docker-ready-blue)](https://hub.docker.com/)

**[📸 Screenshots](#screenshots) · [📖 Docs](#architecture) · [🚀 Quick Start](#quick-start)**

## Deploy

### One-click deploy to Railway
Create a new Railway project from this repo (Railway dashboard → **New Project** →
**Deploy from GitHub repo**); `railway.toml` in the repo root supplies the build and
healthcheck config.

**Required environment variables to set in Railway dashboard:**
- `GEMINI_API_KEY` — for L4 LLM explanations and all other AI features (optional but recommended)
- `GITHUB_TOKEN` — for GitHub API access (optional; 60 req/hr without)
- `ARCHGUARD_DASHBOARD_TOKEN` — secures the dashboard API with Bearer auth
- `ALLOWED_ORIGINS` — comma-separated frontend domains (e.g. `https://your-app.vercel.app`)
- `ENVIRONMENT` — set to `production` for secure session cookies (already set in `railway.toml`'s `[env]` section)
- `ARCHGUARD_TRUSTED_PROXY_IPS` — must be set to the hosting platform's actual proxy range for per-user rate limiting to function correctly (set in the Railway dashboard).

### Deploy to Render
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/jainamsethia/ArchGuard)

Set the same environment variables in the Render dashboard under **Environment**. Note that `ARCHGUARD_TRUSTED_PROXY_IPS` is already configured directly in `render.yaml` with the appropriate CIDR range.

### Run locally with Docker Compose
```bash
cp .env.example .env
# Edit .env to add your API keys
docker compose up
# Open http://localhost:8000
```
## Architecture

![Architecture](docs/architecture.png)

```mermaid
flowchart TB
subgraph Input["📥 Input"]
GH[GitHub repository URL]
WEB[Web dashboard]
end
subgraph Pipeline["🔍 Analysis Pipeline"]
direction TB
L1["Layer 1: Import Boundaries\n(tree-sitter AST)"]
L2["Layer 2: Coupling Delta\n(NetworkX fan-out)"]
L3["Layer 3: Semantic Drift\n(MiniLM embeddings)"]
L4["Layer 4: Duplication\n(FAISS vector search)"]
EXPLAIN["LLM Explanation\n(Gemini)"]
SCORE["🧮 ArchDebt Scoring\n(weighted composite)"]
end
subgraph Cache["💾 Cache Layer"]
SQLITE[(SQLite WAL\nEmbedding Cache)]
INCR[SHA-256\nIncremental Hash]
end
subgraph Output["📤 Output"]
COMMENT[PR Comment]
HTML[HTML Report]
AUDIT[Audit JSONL Log]
EXIT[CI Exit Code]
end
subgraph Contract["📋 Contract"]
YAML[.archguard.yml\n(JSON Schema v3.0)]
REINFER[Re-inference\nEngine]
end
Input --> Pipeline
Pipeline --> SCORE
SCORE --> Output
Cache -.->|Cache reads| Pipeline
Pipeline -.->|Cache writes| Cache
Contract --> Pipeline
L3 -->|persistent drift| REINFER
REINFER --> Contract
SCORE -->|violations| EXPLAIN
EXPLAIN --> Output
```

ArchGuard runs a 4-layer analysis pipeline on every PR:

| Layer | Signal | Technology |
|-------|--------|------------|
| Boundary | Forbidden cross-module imports | tree-sitter AST |
| Coupling | Fan-out exceeds budget | NetworkX graph |
| Semantic | Embedding centroid drift | MiniLM + cosine similarity |
| Duplication | Cross-module function clones | FAISS vector search |

Results posted as a PR comment with an ArchDebt score and LLM-generated explanation.


## Screenshots

None yet. The three placeholders that used to sit here described terminal
output and an automated PR comment from the removed CLI, so they promised a
product that no longer exists rather than a screenshot that had not been taken.

## Technical Highlights
- **4-layer analysis**: AST parsing + graph coupling + ML embeddings + FAISS vector search
- **Louvain community detection** on commit co-change graph for automatic contract generation
- **Incremental analysis**: SHA-256 file hashing + SQLite WAL cache — only recomputes changed files
- **Resilient LLM explanations**: Gemini Flash (primary) with automatic fallback to Gemini Flash-Lite on rate-limit, server error or timeout, dispatched with concurrent async calls.
- **Re-inference engine**: proposes contract updates when semantic drift persists across PRs

## Phase 3: Architecture Intelligence

ArchGuard integrates multiple intelligent components to actively evaluate and guide architectural evolution:

### Architecture fitness functions
Rules declared in `.archguard.yml` — no cycles between named modules, a module
that must not grow past a size, a dependency direction that must hold. They are
evaluated on every analysis and a failing critical gate caps the grade, so a
repository cannot present an A while carrying a cycle.

### AI Advisor
An LLM-backed panel that answers questions about the analysis it is looking at,
streaming through the Gemini API. Needs `GEMINI_API_KEY`; without one it
reports itself unavailable rather than failing quietly.

### Architecture evolution
Walks a repository's git history, analysing selected commits in worktrees, and
charts how health moved across them. Bounded by `ARCHGUARD_EVOLUTION_TIMEOUT`,
because each commit examined is a full four-layer analysis.

### AI remediation plans
Turns a specific violation into ordered refactoring steps. Also Gemini-backed
and also optional.

### Compare runs
Two runs of the same repository side by side: what was fixed, what appeared,
and how each module's score moved.

### Watched repositories
Re-analysed on a schedule. When health drops past a threshold you set, a
fitness gate starts failing, or a new critical issue appears, it is recorded
and — if you configured a webhook — sent to it. Outbound URLs are checked
against an SSRF guard that resolves the hostname once and connects to the
address it approved.

### Incremental re-analysis
A rescan reuses what has not changed, keyed on content hashes stored per
repository. The reuse is invisible: an incremental result is required to be
identical to a full one for the same repository state, and the test suite
compares them.

### Dependency Health Score
Native integration with `pip-audit` to evaluate real-time third-party vulnerability awareness, merging software supply chain health into overall project architectural scoring.

## Using it

ArchGuard is a website. There is no command-line tool: `pip install archguard`
installs an importable library and no executable, and the analysis engine is
driven by the web app and its worker rather than by a shell.

To analyse a repository:

1. Sign in with GitHub at `/`.
2. Paste a public GitHub URL and submit.
3. Watch the four layers report progress live, then read the result on the
   dashboard.

The repository is cloned anonymously over public HTTPS — ArchGuard never acts
on your behalf and asks GitHub only for `read:user`. If the repository has no
`.archguard.yml`, one is generated for it from its own co-change history.

Everything else is on the dashboard: per-module scores, the violation list,
dependency vulnerabilities, evolution over time, Compare Runs, and watching a
repository so a regression finds you instead of the other way round.

### Running your own instance

`docs/DEVELOPMENT.md` covers local setup; `docs/DEPLOYMENT.md` covers a real
deployment. The short version is above under **Deploy**.

## What a result looks like

Every analysis produces a health score out of 100, a letter grade, a band
(PASS / WARN / FAIL) and a list of violations, each attributed to a module with
a severity. The dashboard shows them per layer, and says explicitly when a
layer could not be measured rather than scoring it as clean:

```text
Layer 1 — import boundaries: not checked (no import rules declared in this
          contract - no boundaries to enforce). Excluded from the score.
Layer 2 — coupling: checked, 1 finding.
Layer 3 — semantic drift: not checked (no prior baseline - semantic drift is
          not available on a first scan of a repository). Excluded from the score.
Layer 4 — duplication: checked, no findings.
```

The score is an average over the layers that produced a signal, reweighted
around the ones that did not. A repository nothing could be measured on does
not come back healthy — it comes back as not measured.

## Tracking health over time

Every run is stored, so the dashboard draws the trend for a repository and
**Compare Runs** puts two of them side by side: what was fixed, what appeared,
and how each module moved. Nothing is parsed from a local file; the history is
whatever the database holds for your account.

**Watched repositories** turn that from something you check into something that
finds you. A watched repository is re-analysed on a schedule, and when its
health drops past a threshold you choose, a fitness gate starts failing, or a
new critical issue appears, ArchGuard records it — and calls your webhook if
you configured one.

## Dashboard workflow

1. Submit a GitHub URL at `/`.
2. A Server-Sent Events stream reports progress as each layer runs.
3. On completion you land on `dashboard.html?job_id=...` with the new run shown.

Arriving at the dashboard with no runs yet gives you an empty state pointing
back to the submit page — distinct from a failed load, which says what went
wrong and offers a retry.

## Configuration profiles

A repository that ships an `.archguard.yml` is analysed against it. One that
does not gets a contract generated per scan, with thresholds from a fixed
preset — `ci` by default (coupling fan-out ≤ 10), configurable per deployment
through `ARCHGUARD_DASHBOARD_PROFILE`. It is a deployment setting rather than a
per-analysis choice: the contract is generated and graded in the same pass, and
a baseline derived from the repository's own measurements would mean no
repository could ever fail its first scan.

## Using it from CI

There is no ArchGuard GitHub Action and no CLI to invoke from one. Earlier
versions shipped both; they were removed with the CLI, and nothing has replaced
them.

What a CI job can do today is talk to a running instance over its HTTP API:
submit a repository, poll the job, and read the result. The endpoints are the
same ones the dashboard uses, they require a signed-in session or the operator
credential, and they are not yet a stable published contract — treat them as
internal until they are versioned as something other than `/api/v1` by
convention alone.

## Environment variables

`.env.example` is the reference: every variable the code reads appears there
with what it does, whether it is required, and whether it applies only to
development or only to tests. It is checked against the source by
`tests/unit/test_env_documentation.py`, so it cannot drift silently.

The seven that stop a **production** deployment from starting if they are
missing or wrong:

| Variable | Why it stops the boot |
|---|---|
| `SESSION_SECRET` | Signs session cookies. Refused under 32 characters, or if it equals `ARCHGUARD_DASHBOARD_TOKEN`. |
| `GITHUB_OAUTH_CLIENT_ID` / `_SECRET` | Without an OAuth app nobody can sign in, and the loopback development fallback is enabled instead. |
| `DATABASE_URL` | Users, jobs, runs, findings, suppressions and watches live in PostgreSQL. |
| `REDIS_URL` | Sessions, rate limits, job progress and the analysis queue. |
| `ALLOWED_ORIGINS` | Credentialed CORS. `*` is refused outright. |
| `ARCHGUARD_TRUSTED_PROXY_IPS` | Without it every request is attributed to the proxy and all users share one rate-limit bucket. |

`ENVIRONMENT=production` is what arms that gate, the Secure cookie flag and
HSTS. `ARCHGUARD_DASHBOARD_ALLOW_REMOTE` is not a discouraged option in
production — it is a boot failure.

`ARCHGUARD_DASHBOARD_TOKEN` is optional: it is an operator credential for
reaching the API without a browser, and signed-in users are unaffected by its
absence.

## Testing

`docs/DEVELOPMENT.md` has the full setup, including the two ways a run can look
clean while testing nothing: `.env` is not read by pytest, and Layers 3 and 4
skip without the `worker` extras.

```bash
set -a; . ./.env; set +a
poetry install --with dev --extras worker
pytest -m "" --no-cov -rs      # everything, with skip reasons shown
npm ci && npm test             # frontend
```

## FAQ

**How do I use a local LLM instead of Gemini?**
You can't. Gemini is the only LLM backend. An unwired Ollama backend used to
ship in `archguard.llm.local`; it was never reachable, so it was removed rather
than left as a feature the docs implied existed. To run with no LLM at all,
leave `GEMINI_API_KEY` unset — the four analysis layers do not use it, and the
AI Advisor and remediation panels report themselves unavailable.

**How do I suppress a false positive?**
On the dashboard, from the violation itself. Suppressions are per account: they
change your view of a repository and nobody else's.

**What does the health score mean?**
0–100, measured against the contract — either the one the repository ships or
the one generated for it. It is an average over the layers that produced a
signal, reweighted around any that could not run, so it never presents an
unmeasured layer as a clean one.

**Can I run it against a private repository?**
No. Repositories are cloned anonymously over public HTTPS, and the OAuth app
asks only for `read:user`, so ArchGuard has no credential that could reach a
private repository.

## Known Limitations

- **Python Version Skew in Standard Library Classification**: The engine uses the standard library module list of the Python version it is currently running on. If ArchGuard is analyzing a repository that targets a different Python version (e.g. running under Python 3.10 but analyzing a codebase that uses `tomllib` from Python 3.11), a small number of version-boundary standard library modules may be misclassified as third-party imports.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details on our code of conduct and the process for submitting pull requests to us.

## Audit Log Security

ArchGuard maintains an append-only JSONL audit log to track structural history. To prevent tampering:
- By default, ArchGuard generates a random 32-byte HMAC key on first run, persisting it to `/app/.archguard-cache/audit.key` inside the container (mounted from the archguard-cache volume on Docker Compose, a Render Disk, or a Railway Volume, depending on platform. Note: Railway's volume attachment is a dashboard/IaC step, not part of the checked-in railway.toml) with strict permissions.
- You can override this by setting `ARCHGUARD_AUDIT_SECRET` in your environment.
- In CI/CD or production environments, you should set `ARCHGUARD_AUDIT_STRICT=1` to enforce that a secure secret is provided (or a key file is already present).

## Security

See [SECURITY.md](SECURITY.md) for information on our security policy and how to report vulnerabilities.

## License

MIT
