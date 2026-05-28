# Contributing to ArchGuard
## Development Setup
1. Clone the repo: `git clone https://github.com/YOUR/archguard`
2. Install with dev extras: `pip install -e ".[dev]"`
3. Install pre-commit hooks: `pre-commit install`
## Running Tests
- Unit tests: `pytest tests/ -v`
- Integration tests: `pytest tests/integration/ -v`
- With coverage: `pytest --cov=archguard`
- Benchmarks: `pytest tests/benchmarks/ --benchmark-only`
## Code Style
- Linting: `ruff check .`
- Formatting: `ruff format .`
- Type checking: `mypy archguard/`
## Making Changes
1. Create a feature branch: `git checkout -b feature/your-feature`
2. Make changes with tests
3. Ensure `make lint` and `make test` pass
4. Submit a pull request with a clear description
## Submitting Issues
Include: Python version, OS, ArchGuard version (`archguard --version`), and the 
`.archguard.yml` contract if relevant.
