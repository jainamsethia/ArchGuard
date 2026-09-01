# Local development setup

ArchGuard needs PostgreSQL and Redis. Nothing falls back to a file store: runs,
jobs, suppressions and sessions all live in those two services.

## The short version

```bash
docker compose up -d postgres redis
```

```bash
cp .env.example .env    # then fill in the two URLs below
```

```bash
poetry install --with dev
```

```bash
alembic upgrade head
```

```bash
make dev
```

`.env` must define:

```
DATABASE_URL=postgresql+asyncpg://archguard:archguard_local_dev@127.0.0.1:5432/archguard_dev
TEST_DATABASE_URL=postgresql+asyncpg://archguard:archguard_local_dev@127.0.0.1:5432/archguard_test
REDIS_URL=redis://127.0.0.1:6379/0
```

`archguard_local_dev` is the password `docker-compose.yml` uses unless you
override `POSTGRES_PASSWORD`.

**`.env` is read by the application, and by nothing else.** `load_dotenv()` is
called in `archguard/dashboard/app.py`, so `make dev` picks it up. pytest does
not, `alembic` does not, and `scripts/dev_services.py` does not — they read the
process environment. A populated `.env` and a running database therefore still
produce:

```
SKIPPED [1] tests/db_fixtures.py: TEST_DATABASE_URL is not set
```

which is the suite telling the truth about a variable it genuinely cannot see.
Export them into the shell before running anything but the app:

```bash
set -a; . ./.env; set +a          # bash/zsh, and Git Bash on Windows
```

CI does the same thing by declaring them as job-level `env:` rather than by
loading a file.

**The compose file creates only `archguard_dev`.** `TEST_DATABASE_URL` above
points at a second database that does not exist until you create it, and
without it the integration tests fail in `alembic upgrade` rather than skipping:

```bash
docker compose exec postgres createdb -U archguard archguard_test
```

**Write `127.0.0.1`, not `localhost`.** On WSL2 (and anywhere else that
resolves `localhost` to `::1` first without listening on IPv6) every connection
waits out the IPv6 attempt before falling back. Measured here: 2.13 s per
connect via `localhost`, 0.09 s via `127.0.0.1` — a 23x difference that turns
the test suite from ten minutes into ten seconds, with nothing in the output to
say why.

`.env` is gitignored. Never commit a database URL: it carries a password.
`alembic.ini` deliberately does not contain `sqlalchemy.url` for the same
reason — `archguard/db/migrations/env.py` reads `DATABASE_URL` instead.

## Health and metrics

| Endpoint | Answers | On failure |
|---|---|---|
| `/health` | Is this process alive? | never fails while it is running |
| `/ready` | Can it serve a request? | 503, naming which dependency is down |
| `/metrics` | Prometheus text | reports `archguard_database_up 0` rather than 500ing |

Point platform health checks at `/ready`, not `/health`. A check that returns
200 whenever the process is alive reports a service as healthy while its
database is unreachable and every request is failing, which stops the platform
rolling back. `/health` exists for the opposite reason: a failing liveness
check kills the container, so it must not depend on anything outside the
process.

## Running the analysis worker

Analyses run in a separate process:

```bash
arq archguard.worker.main.WorkerSettings
```

Without one, the web process runs them itself and says so in the log. That is a
development convenience, not a deployment: it cannot survive a restart, it is
not shared between instances, and it puts untrusted repository parsing in the
process holding every session key. The production config check refuses to start
without `REDIS_URL`, which is what the queue lives in.

`docker compose up` starts both services.

## Without Docker

Any PostgreSQL 14+ and Redis 6+ will do. Create the role and the two
databases:

```sql
CREATE ROLE archguard LOGIN PASSWORD 'choose-a-local-password' CREATEDB;
CREATE DATABASE archguard_dev  OWNER archguard;
CREATE DATABASE archguard_test OWNER archguard;
```

`archguard_test` is separate because the integration tests migrate it up and
tear it back down to base; pointing them at your development database would
drop your data.

### Windows without Docker Desktop

WSL2 is the least troublesome route — it gives real Linux packages, and WSL's
localhost forwarding makes them reachable from Windows at `127.0.0.1:5432` and
`127.0.0.1:6379` with no extra configuration. Use the IPv4 literal, for the
reason given above.

```bash
wsl -d Ubuntu -u root -- apt-get update
```

```bash
wsl -d Ubuntu -u root -- apt-get install -y postgresql redis-server
```

```bash
wsl -d Ubuntu -u root -- systemctl start postgresql redis-server
```

One caveat worth knowing: WSL shuts the VM down when no process is running in
it, which stops both services. Keep something alive in the distro for the
duration of a session:

```bash
wsl -d Ubuntu -u root -- sleep infinity &
```

## Running the tests

Everything below assumes you have exported `.env` into the shell, as above.
Without that the database tests skip and the run still reports success.

The default run excludes tests marked `integration`:

```bash
pytest
```

It is not, however, service-free: `testpaths` includes `tests/integration`, and
the files there that carry no `integration` marker do run. Most self-skip
without a database; a few genuinely execute.

To include the database tests, clear the marker filter — and turn coverage off,
because `addopts` carries `--cov-fail-under=79` and a partial run cannot reach
it, so the command exits 1 with every test passing:

```bash
pytest -m "" --no-cov                       # everything
pytest tests/integration/ -m "" --no-cov    # just the integration suite
```

### Layers 3 and 4 need the ML extras

`poetry install --with dev` does not install them, so `faiss` and
`sentence-transformers` are missing and every Layer 3 and Layer 4 test skips.
The default `addopts` has no `-rs`, so nothing says so — the run looks clean.

```bash
poetry install --with dev --extras worker
pytest -m "" --no-cov -rs                   # -rs prints why anything skipped
```

Leave `ARCHGUARD_SKIP_ML` **unset**. Setting it short-circuits both layers
before their runners, so the tests pass without exercising the code they are
about. The CI `ml` job exists for exactly this reason: it installs the extras,
deliberately leaves that variable unset, and then asserts that nothing skipped
for want of ML. A defect that reached production once already hid in that gap.

### Frontend, browser and smoke suites

```bash
npm ci                                      # once, per clone
npx playwright install --with-deps chromium # once, for the browser suites
npm test                                    # jsdom, Node's built-in runner
```

The browser suites need the dashboard on port **8765** — not the 8000 that
`make dev` serves — with three variables the suites depend on. Start it
yourself and tell Playwright to reuse it:

```bash
SESSION_SECRET=local-dev-only-0123456789abcdef0123456789abcdef \
ARCHGUARD_RATE_LIMIT_MAX_REQUESTS=100000 \
ARCHGUARD_MOCK_LLM=1 \
  poetry run uvicorn archguard.dashboard.app:app --host 127.0.0.1 --port 8765 &

PLAYWRIGHT_REUSE_SERVER=1 npx playwright test tests/a11y tests/e2e
PLAYWRIGHT_REUSE_SERVER=1 npx playwright test tests/visual --grep-invert "@snapshot"
```

`PLAYWRIGHT_REUSE_SERVER=1` is what makes Playwright use your server instead of
starting its own — which also means `webServer.env` in `playwright.config.ts`
does not apply, and those three variables are yours to supply. Without
`SESSION_SECRET` the session module raises; without the rate-limit bump
`/api/v1/auth/status` starts answering 429 partway through and the sign-in
overlay covers the controls under test, which reads as a flaky selector.

Omit `PLAYWRIGHT_REUSE_SERVER` and Playwright starts and configures a server
itself. That is the simpler path everywhere except Windows, where the managed
server wedges the runner (see the note in `playwright.config.ts`).

The rate limiter counts per IP in Redis, shared across processes. Running the
browser suites and then the smoke script inside the same minute makes the
second one see the first one's budget and fail with 429s that look like real
failures.

```bash
poetry run uvicorn archguard.dashboard.app:app --host 127.0.0.1 --port 8000 &
BASE_URL=http://localhost:8000 bash scripts/smoke_test.sh
```

### Running what CI runs

| Job | Command |
|---|---|
| `test` | `pytest -m "not slow"` |
| `ml` | `poetry install --extras worker`, then `pytest tests/unit/ tests/integration/ -m "integration or not integration" --no-cov` with `ARCHGUARD_SKIP_ML` unset |
| `frontend` | `npm ci && npm test` |
| `migrations` | `alembic upgrade head && alembic check` |
| `visual-regression` | `npx playwright test tests/visual/ --grep-invert "@snapshot"` |
| `visual-snapshots` | `npx playwright test tests/visual/ --grep "@snapshot"` — advisory, see below |
| `accessibility` | `npx playwright test tests/a11y/` and `tests/e2e/` |
| `lint` / `security` | `ruff check archguard/`, `mypy archguard/ --ignore-missing-imports`, `bandit -r archguard/ -ll` |

### Visual snapshots

Two of the visual tests compare screenshots, and screenshots belong to the
machine that took them — the same page renders thousands of pixels differently
on Windows and on Linux through font hinting alone. Only the `-linux.png`
baselines are committed, because Linux is what CI compares against; `-win32`
and `-darwin` images are gitignored so a local run cannot commit its own
rendering as the reference.

That means a deliberate UI change cannot have its baseline refreshed from a
Windows or macOS checkout. Run the **Regenerate visual baselines** workflow
from the Actions tab instead, download the `linux-visual-baselines` artifact,
copy it over `tests/visual/snapshots/` and commit it. Regenerating is a claim
that the new rendering is correct, which is why it is a manual dispatch rather
than something that happens on every push.

Locally the snapshot tests compare against your own platform's baselines, which
Playwright writes on first run. They are useful for catching an accidental
change in the same session, and they are not what CI checks:

```bash
PLAYWRIGHT_REUSE_SERVER=1 npx playwright test tests/visual --update-snapshots
```

## Migrations

Autogenerate after changing a model in `archguard/db/models.py`:

```bash
alembic revision --autogenerate -m "what changed"
```

Always read the generated file before committing it. Autogenerate does not
detect renames — it emits a drop plus an add, which silently discards the
column's data.

Verify a migration both ways before committing. A downgrade that does not undo
its upgrade is discovered during a rollback, which is the worst moment to find
out:

```bash
alembic upgrade head && alembic downgrade base && alembic upgrade head
```

`alembic check` fails when a model has drifted from the migrations, and runs in
CI.

## Verified reference environment

The setup these instructions were written and tested against:

| | |
|---|---|
| OS | Windows 11 26200 (ARM64), WSL2 |
| WSL distro | Ubuntu 26.04 LTS (aarch64), kernel 6.6.87.2 |
| PostgreSQL | 18.6 (`postgresql` 18+290ubuntu1) |
| Redis | 8.0.5 |
| Python | 3.11.9 |
| Poetry | 2.4.1 |
| Node | 24.16.0 |
| asyncpg / SQLAlchemy / Alembic | 0.31.0 / 2.0.52 / 1.19.1 |
| redis-py | 8.1.0 |

## WSL2 and the local services

On Windows, PostgreSQL and Redis run inside the WSL2 Ubuntu distro, and WSL2
shuts an idle VM down -- taking both with it. Measured on WSL 2.5.10: the VM was
gone after 100 seconds of inactivity. `.wslconfig`'s `vmIdleTimeout` does not
prevent it (tried at `-1` and at a week in milliseconds; the key is accepted and
the VM still stops). An *attached* session does.

The failures this causes do not look like what they are:

| What you see | What it is |
|---|---|
| pytest takes an hour instead of three minutes | every test paying a 3s Redis connection timeout |
| `alembic upgrade failed` in a fixture | PostgreSQL is not running |
| four endpoint 500s from `smoke_test.sh` | neither service is running |

`pytest` holds a session open for its own duration automatically, so the test
suite needs nothing from you. For anything else -- a dev server, the smoke
script, a Playwright run -- hold one yourself in a second terminal:

```bash
make services          # holds until Ctrl-C
```

or check reachability without holding anything:

```bash
make check-services
```

`make dev` depends on the check, so it fails fast with a readable message
rather than serving 500s. Both are no-ops on Linux and macOS, and on any setup
whose `DATABASE_URL` points somewhere other than loopback.

They are also a no-op when the URLs are only in `.env`, for the same reason the
tests skip: the script reads the process environment, so with nothing exported
it finds no services configured, prints `No DATABASE_URL or REDIS_URL
configured; nothing to check` and exits 0. Export `.env` first or the check
passes by having nothing to check.
