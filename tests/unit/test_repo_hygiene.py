import pathlib

_ALLOWED_ROOT_FILES = {
    ".archguard.yml",
    ".dockerignore",
    ".gitignore",
    ".pip-audit-ignore",
    ".pre-commit-config.yaml",
    ".shellcheckrc",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "Dockerfile",
    "Makefile",
    "README.md",
    "SECURITY.md",
    "pyproject.toml",
    "poetry.lock",
    "poetry.toml",
    "coverage.xml",
    ".env.example",
    ".env",  # documented local dev file (README: cp .env.example .env); gitignored, never committed
    "docker-compose.yml",
    "railway.toml",
    "render.yaml",
    "package.json",
    "package-lock.json",
    "playwright.config.ts",
}


def test_repository_root_has_no_unexpected_files():
    """Guards against ad hoc scripts/output dumps re-accumulating at the repo
    root, as happened across prior Phase 1/3 audit sessions."""
    repo_root = pathlib.Path(__file__).resolve().parents[2]

    actual = {
        p.name
        for p in repo_root.iterdir()
        if p.is_file() and not p.name.startswith(".coverage")
    }

    unexpected = actual - _ALLOWED_ROOT_FILES
    assert not unexpected, f"Unexpected files at repo root: {sorted(unexpected)}"
