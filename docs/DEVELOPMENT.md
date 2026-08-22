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
DATABASE_URL=postgresql+asyncpg://archguard:<password>@127.0.0.1:5432/archguard_dev
TEST_DATABASE_URL=postgresql+asyncpg://archguard:<password>@127.0.0.1:5432/archguard_test
REDIS_URL=redis://127.0.0.1:6379/0
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

The default run excludes integration tests, so it needs no services:

```bash
pytest
```

The database tests are marked `integration` and skip themselves when
`TEST_DATABASE_URL` is unset. To run them:

```bash
pytest tests/integration/ -m "" 
```

Frontend tests use jsdom and Node's built-in runner:

```bash
npm test
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
