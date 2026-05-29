## Development Setup
```bash
git clone https://github.com/[username]/ArchGuard
cd ArchGuard
python -m venv venv && source venv/bin/activate
pip install -e ".[dev,ml]"
pre-commit install
pytest tests/unit -v
```

## Running the Full Test Suite
```bash
pytest tests/ -v --cov=archguard --cov-report=html
```

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
