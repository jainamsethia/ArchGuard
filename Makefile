.PHONY: test lint typecheck benchmark benchmark-check smoke-test

test:
	poetry run pytest tests/unit -v --tb=short

lint:
	poetry run ruff check archguard/

typecheck:
	poetry run mypy archguard/ --ignore-missing-imports

benchmark:
	poetry run pytest tests/benchmarks/ --benchmark-only --benchmark-json=benchmark-results.json -v
	python3 scripts/check_benchmarks.py benchmark-results.json

benchmark-check:
	python3 scripts/check_benchmarks.py benchmark-results.json

smoke-test:
	bash scripts/smoke_test_docker.sh

# ── Phase 2: Dashboard & Docker ──────────────────────────────────────────

dev:
	poetry run uvicorn archguard.dashboard.app:app --reload --port 8000 --log-level info

dev-debug:
	poetry run uvicorn archguard.dashboard.app:app --reload --port 8000 --log-level debug

docker-build:
	docker build -t archguard:local .

docker-up:
	docker compose up --build

docker-up-detached:
	docker compose up -d --build

docker-down:
	docker compose down -v

docker-logs:
	docker compose logs -f

# ── Cleanup ──────────────────────────────────────────────────────────────

clean:
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -delete 2>/dev/null || true
	@rm -rf .archguard-cache/ /tmp/archguard-* 2>/dev/null || true
	@echo "🧹  Clean."

clean-all: clean
	@rm -rf .venv/ .venv-test/ dist/ build/ *.egg-info/ 2>/dev/null || true
	@echo "🧹  Deep clean."

# ── Full test with coverage ───────────────────────────────────────────────

test-full:
	poetry run pytest tests/unit tests/integration \
		--cov=archguard \
		--cov-report=term-missing \
		--cov-fail-under=70 \
		-v --tb=short

setup:
	bash scripts/setup.sh
