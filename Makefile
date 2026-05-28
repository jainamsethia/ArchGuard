.PHONY: test lint typecheck benchmark benchmark-check

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
