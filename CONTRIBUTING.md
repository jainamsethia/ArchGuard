## Development Setup

The project uses Poetry, and the only extra it defines is `worker` (layers 3
and 4, which pull in torch and are large). Everything else is in the main and
dev groups.

```bash
git clone https://github.com/jainamsethia/ArchGuard
cd ArchGuard
poetry install --with dev
poetry run pre-commit install
```

Analyses read and write PostgreSQL and Redis, so most tests need both:

```bash
cp .env.example .env   # set DATABASE_URL, TEST_DATABASE_URL, REDIS_URL, SESSION_SECRET
docker compose up -d postgres redis
poetry run alembic upgrade head
```

Point `TEST_DATABASE_URL` at a **separate** database. The integration tests
migrate it up and tear it back down to base.

## Running the Tests

```bash
poetry run pytest tests/unit tests/integration
```

```bash
npm ci && npm test
```

```bash
npx playwright install chromium webkit && npx playwright test tests/visual/ tests/a11y/
```

The coverage gate is 79% and lives in `pyproject.toml`, so `poetry run pytest`
enforces it without a flag. Database-backed tests skip loudly rather than
silently when `TEST_DATABASE_URL` is unset.

## Running Benchmarks
```bash
make benchmark
```

## Making a PR
1. Fork the repo
2. Create a branch: `git checkout -b fix/your-fix`
3. Make changes and add tests
4. Run: `make lint test`
5. Submit PR with description of the change
