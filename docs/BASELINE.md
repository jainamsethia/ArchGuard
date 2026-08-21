# Verified Pre-Implementation Baseline

**Recorded at:** commit `793022d` (working tree clean, nothing pushed to `origin/main`)
**Date:** 2026-08-20
**Purpose:** the reference every subsequent task compares against. Nothing was
fixed to make these numbers look better; the one failure that appeared during
baselining was caused by this reconciliation itself and is noted as such.

---

## 1. Environment

| | |
|---|---|
| Python | 3.11.9 (pyenv-win) |
| Platform | Windows-10-10.0.26200-SP0 |
| Virtualenv | `.venv/` — base + dev groups |
| ML extras (`torch`, `faiss-cpu`, `sentence-transformers`) | **not installed locally** |
| Docker | **not available in this environment** |
| Git hooks | none installed (`.git/hooks/` empty of active hooks) |

This matters when reading the numbers below: three ML-dependent tests skip
locally that would run in CI, and the whole Docker section could not be
measured here.

---

## 2. Test suite

Command (matches the CI `test` job, plus `--cov-branch`):

```bash
ARCHGUARD_TEST_MODE=1 ARCHGUARD_SKIP_ML=1 ARCHGUARD_MOCK_LLM=1 \
  pytest tests/unit/ tests/integration/ --cov-branch --cov-report=term --timeout=120 -m "not slow"
```

```
790 passed, 3 skipped, 2 deselected, 9 warnings in 90.60s
exit code 0
```

### Skipped (3)

| Test | Reason |
|---|---|
| `tests/unit/test_duplication.py:8` | faiss-cpu not installed |
| `tests/integration/test_fixture_correctness.py:201` | requires ML extras |
| `tests/integration/test_fixture_correctness.py:235` | requires ML extras |

`test_planted_duplication_detected_with_ml` — recorded in `.pytest_cache` as the
last known failure — is one of the two `test_fixture_correctness.py` skips here.
It **skips** rather than fails without ML extras, so its true status is unknown
locally and must be re-measured in CI, where the extras are installed. Treat it
as **unresolved**, not as passing.

### Deselected (2)

Two `integration`-marked tests excluded by `-m "not slow"` combined with the
`addopts` marker filter in `pyproject.toml`.

---

## 3. Coverage

**Branch coverage is measured here for the first time** — `--cov-branch` was
never enabled, so the historical 78.9% figure counted every untested `else`
as covered.

| Metric | Value |
|---|---|
| Line coverage | **79.76%** |
| Branch coverage | **68.54%** |
| Configured gate (`--cov-fail-under`) | 76% (line only — passes) |

### Per package, weakest first

| Package | Line | Branch |
|---|---:|---:|
| `alerting` | 37.5% | **7.1%** |
| `risk` | 76.6% | 56.2% |
| `contract` | 76.5% | 66.4% |
| `cli` | 77.6% | 65.6% |
| `analysis` | 75.3% | 68.2% |
| `cache` | 79.5% | 64.9% |
| `dashboard.routes` | 81.5% | 65.4% |
| `evolution` | 77.6% | 71.2% |
| `dashboard` | 83.7% | 68.6% |
| `github` | 81.0% | 72.2% |
| `.` (root modules) | 91.7% | 66.7% |
| `fitness` | 84.3% | 74.3% |
| `llm` | 83.5% | 75.7% |
| `suppression` | 87.2% | 81.2% |
| `utils` | 92.0% | 79.0% |
| `observability` | 97.6% | 75.0% |
| `audit` | 88.1% | 87.5% |
| `profiles` | 100.0% | 85.7% |
| `templates` | 100.0% | 100.0% |

`alerting` at **7.1% branch** is the weakest package in the repository by a wide
margin, and it is one of the two subsystems being preserved through CLI removal.
That gap is exactly where the inverted trend direction (C10) survived.

---

## 4. Lint and type checking

| Check | Command | Result |
|---|---|---|
| Ruff | `ruff check archguard/ tests/ scripts/` | **All checks passed** (exit 0) |
| Mypy (strict) | `mypy archguard/ --ignore-missing-imports` | **Success: no issues found in 131 source files** |

---

## 5. Security scans

### Bandit — clean

```
bandit -r archguard/ -ll   →  exit 0
[tester] WARNING nosec encountered (B608), but no failed test on
         archguard/cache/embeddings.py:192
```

### pip-audit — 7 findings, all local-environment artifacts

```
pip-audit --ignore-vuln PYSEC-2026-196   →  exit 1
Found 7 known vulnerabilities in 1 package

setuptools 65.5.0  PYSEC-2022-43012  fix 65.5.1
setuptools 65.5.0  PYSEC-2025-49     fix 78.1.1
setuptools 65.5.0  PYSEC-2026-1918   fix 70.0.0
setuptools 65.5.0  PYSEC-2026-3447   fix 83.0.0
```

**Not a repository defect.** `pyproject.toml` already floors `setuptools >= 83.0.0`
inside the `ml` extra, precisely for `PYSEC-2026-3447`. The local `.venv` has the
virtualenv-seeded 65.5.0 because the `ml` extra is not installed here. CI runs
`poetry install --with dev --all-extras`, which installs the floored version.
**Re-verify in CI before treating this as green.**

---

## 6. Runtime smoke test

Server started locally:

```bash
ARCHGUARD_DASHBOARD_ALLOW_REMOTE=1 ARCHGUARD_MOCK_LLM=1 \
  uvicorn archguard.dashboard.app:app --host 127.0.0.1 --port 8199
```

`GET /health` → `{"status":"ok","version":"0.3.0","environment":"development","uptime_seconds":12}`

`BASE_URL=http://127.0.0.1:8199 bash scripts/smoke_test.sh` → **exit 127**

| Check | Result |
|---|---|
| Health endpoint returns ok | PASS |
| Health returns a real version (not "unknown") | PASS |
| Frontend main page HTTP 200 | PASS |
| CSP header present | PASS |
| `X-Content-Type-Options: nosniff` | PASS |
| `X-Frame-Options: DENY` | PASS |
| `Referrer-Policy` set | PASS |
| CSP `script-src` contains a nonce | PASS |
| `unsafe-inline` only in `style-src` | PASS |
| POST /api/jobs — invalid URL rejected | **FAIL** (script bug, see below) |
| POST /api/jobs — path traversal rejected | **FAIL** (script bug, see below) |
| everything after check [8] | **not reached** — script aborted |

---

## 7. Findings discovered while baselining

These are new; they are not in the plan's C-list and should be added to it.

### B1 — `scripts/smoke_test.sh` has four structurally broken assertions

`check_status()` is defined as `check_status(name, url, expected_code)` — three
positional parameters. Four call sites pass seven arguments:

```bash
check_status "POST /api/jobs — invalid URL rejected" \
    -X POST "$BASE_URL/api/jobs" \
    -H "Content-Type: application/json" \
    -d '{"github_url":"not-a-url"}' \
    "422"
```

so `$2` is `-X` and `$3` is `POST`. The function curls the URL `-X`, gets `000`,
and compares it against the string `POST` — hence
`expected HTTP POST, got HTTP 000`. Affected: checks [6], [7], [8], and
"Validate endpoint (invalid POST)".

Check [9] is separately wrong: it issues a **GET** to
`/api/jobs/validate`, which is a POST-only route, and expects 200.

The script ends with `exit 1` whenever `FAIL > 0`, and CI's `test` job runs it
unguarded. **Either the CI smoke step is currently failing, or it is not
enforcing what it appears to.** Verify against a real CI run before trusting it.

Two further portability problems, Windows-only and therefore not CI-blocking:
`grep -oP` fails under this locale, and `python3` is not on PATH (line 123),
which is what produced exit 127 rather than exit 1.

### B2 — Direct evidence for C1 (no logging configuration)

The server's own startup output:

```
INFO:     Started server process [2292]
INFO:     Waiting for application startup.
Optional env var GEMINI_API_KEY not set - L4 LLM explanations, ...
ARCHGUARD_DASHBOARD_TOKEN is not set. Authentication relies on ...
ArchGuard dashboard uses in-memory job/session/rate-limit state. ...
INFO:     Application startup complete.
```

The `INFO:`-prefixed lines are uvicorn's own configured logger. Every
`archguard.*` line has no level, no timestamp, no logger name and no JSON —
they are reaching stderr through `logging.lastResort`. No `archguard.http`
access-log line appeared for any request. C1 confirmed against a running
server.

Note also that the first startup warning names "L4 LLM explanations" as a
feature `GEMINI_API_KEY` enables. It is not — see C6.

### B3 — A stale ArchGuard dev server is running on this machine

Port 8123 has an ArchGuard instance with ~86,000s (≈24h) uptime, predating this
session. The first smoke-test attempt bound to 8123, failed with
`[Errno 10048]`, and silently tested that stale server instead of the current
tree. It was **left running and untouched** — it is not this session's process
to terminate. Anyone re-running the smoke test locally should pick an unused
port and confirm `Started server process` appears without a bind error.

---

## 8. Not measurable in this environment

Deferred to CI, and required before the STARTUP-READY gate:

- `docker build` — image size, layer count, build success
- Container `HEALTHCHECK` behaviour
- `docker compose up` end-to-end
- Whether `pip-audit` is on PATH in the runtime image (expected: **no** — C2)
- The true status of `test_planted_duplication_detected_with_ml` with ML extras
- pip-audit against a fully-extra'd environment

---

## 9. Baseline summary

| Gate | Status |
|---|---|
| Tests | 790 passed, 0 failed, 3 skipped |
| Line coverage | 79.76% (gate 76%) |
| Branch coverage | 68.54% (no gate — first measurement) |
| Ruff | clean |
| Mypy strict | clean |
| Bandit | clean |
| pip-audit | 7 findings, all from the local venv's unfloored setuptools |
| Smoke test | **broken script** — 9 checks pass, 2 fail on a bug in the test itself, rest unreached |
| Docker | not measurable here |

Any future run that is worse than this on tests, coverage, ruff, mypy or bandit
is a regression introduced by the work in progress, and must be fixed before the
next task starts.

---

# REASSESS checkpoint — 2026-08-21

The plan's re-audit gate, taken after the thirteen tasks that precede it. Same
verification set as the baseline above, re-run in full. Nothing was fixed to
make these numbers look better; the one failure that appeared is described
below along with why it was a correct failure.

**At commit** `5121578`. 28 commits ahead of `origin/main`; nothing pushed.

## Environment, and how it differs from the baseline

| | Baseline | Now |
|---|---|---|
| PostgreSQL | not available | 18.6, WSL2, reachable at `127.0.0.1:5432` |
| Redis | not available | 8.0.5, WSL2, reachable at `127.0.0.1:6379` |
| Docker | not available | installed but **will not start unattended** |
| Playwright browsers | not installed | Chromium 1228 installed |

The database and Redis are why most of these numbers can be trusted now: at
baseline the persistence layer did not exist, and the tests that would have
exercised it could not have run.

## Results

| Gate | Baseline | Now |
|---|---|---|
| Tests passed | 790 | **784** |
| Tests failed | 1 (`test_planted_duplication_detected_with_ml`) | **0** |
| Line coverage | 79.76% | — (combined figure below) |
| Branch coverage | 68.54% | — |
| Combined coverage (branch on) | — | **80.97%** |
| Coverage gate | 76% | **79%** |
| Integration-marked | not run | **40 passed, 1 skipped** |
| ruff | clean | **clean** |
| mypy | clean | **clean** (112 files) |
| bandit | clean | **clean** |
| pip-audit | 7 findings, all local `setuptools` | **unchanged, same cause** |
| Frontend (jsdom) | did not exist | **7 passed** |
| Browser behaviour | could not fail the build | **8 passed, blocking** |
| Accessibility (axe-core) | did not exist | **6 passed, 2 skipped** |
| Smoke test | 2 assertions structurally unable to pass | **20 passed** |

The test count fell by six while coverage rose, which is the expected shape:
`test_job_manager.py`, `test_job_manager_eviction.py`, `test_cookie_auth.py`
and `test_app_versioning.py` were deleted because the code they tested is gone,
and `test_tenancy.py`, `test_oauth.py`, `test_sessions.py`,
`test_config_check.py`, `test_evolution_bounds.py`, `test_progress_phases.py`,
`test_meta_endpoints.py`, `test_route_structure.py` and
`test_worker_roundtrip.py` were added.

The baseline's known failure is fixed: `test_planted_duplication_detected_with_ml`
now passes.

## The one failure this gate found

`test_github_url_e2e.py::test_submit_job_and_poll_to_complete` polled for five
minutes and the job never left `queued`.

That was correct behaviour, not a regression. Submission hands the job to the
queue now, and no worker was running, so nothing consumed it. The test forces
`ARCHGUARD_INLINE_ANALYSIS=1`; the worker path is covered by
`test_worker_roundtrip.py` against a real arq worker.

Worth recording as a product observation: **a deployment with `REDIS_URL` set
and no worker running leaves every job queued indefinitely, and the user sees a
spinner that never stops.** `/metrics` exposes it
(`archguard_jobs_total{status="queued"}` and `archguard_queue_depth`), so an
operator can see it, but nothing tells the user. A stale-job reaper is not yet
written.

## Verified against real services, not mocks

- Migration `upgrade -> downgrade -> upgrade` on PostgreSQL 18.6: 8 tables -> 0 -> 8.
- `alembic check`: no model drift.
- A job enqueued from the web side, consumed by `arq
  archguard.worker.main.WorkerSettings`, cloning benjaminp/six and writing
  `complete / 100 / A` to PostgreSQL, with an 11-event progress stream the web
  process read back in full.
- Analysis progress strictly increasing 3 -> 12 -> 28 -> 34 -> 42 -> 55 -> 78
  -> 92 -> 100 across a real run.
- Redis stopped mid-flight: `/ready` returned 503 naming `redis: PING failed`
  while the other three checks still reported, `/health` stayed 200, and
  restarting Redis restored `/ready` to 200.
- A production-configured process with `ALLOWED_ORIGINS='*'` refused to start
  and named all four problems.

## Still unverified

**`docker build --target web` and `--target worker`.** Docker Desktop is
installed on this machine but its WSL distros will not start unattended, so the
two assertions — the web image cannot import torch; the worker image has
pip-audit on PATH and loads the embedding model with `--network none` — are
written into the CI `docker` job and have not been executed anywhere yet.

## Deliberate deviations from the plan

- **P0-5** bounds `/evolution/analyze` in place (ceiling 100 -> 20, per-user
  single-flight lock in Redis, 300s timeout, work moved off the event loop)
  rather than routing it through the arq queue. The amplification is closed by
  the lock, not by where the work runs; queueing it would change a synchronous
  API the frontend depends on and make history analyses compete with real ones
  for worker slots.
- **Visual snapshot comparisons** are an advisory CI job rather than a blocking
  one. Baselines are platform-specific and go stale whenever the UI changes
  deliberately, which is not a regression. The behaviour tests in the same file
  are blocking. The committed `-linux` baselines predate the determinate
  progress bar and the sign-in overlay and need regenerating on Linux.
