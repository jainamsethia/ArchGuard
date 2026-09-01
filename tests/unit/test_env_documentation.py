"""`.env.example` has to describe the environment the code actually reads.

It had drifted both ways. Two variables were documented that nothing had read
since the CLI was removed, so an operator could set `ARCHGUARD_SKIP_LLM=1` and
watch it do nothing. Seven that the code does read were absent, including
`ARCHGUARD_DATA_DIR`, whose misconfiguration the production gate refuses to
start on. And the whole observability section appeared four times, because a
file nobody diffs is a file nobody notices duplicating.

The variables are found by parsing the source rather than by grepping it: a
grep for `os.environ` misses `os.environ.get(DATABASE_URL_ENV)`, which is how
the single most important variable in the project is read, and a test that
missed it would have licensed removing it.

Deliberately not a documentation framework. Three questions -- is everything
read documented, is everything documented read, and is anything said twice --
which are the three ways this file has actually gone wrong.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "archguard"
EXAMPLE = ROOT / ".env.example"

#: Read by the tests or the tooling rather than by the application, so they
#: belong in the file but will never be found in `archguard/`.
NON_APPLICATION = {
    "TEST_DATABASE_URL",
    "PLAYWRIGHT_REUSE_SERVER",
}

#: Removed with the CLI. Nothing reads them, and documenting them invites an
#: operator to set something that silently does nothing.
KNOWN_DEAD = {
    "ARCHGUARD_SKIP_LLM",
    "ARCHGUARD_SLACK_WEBHOOK",
    "ARCHGUARD_LLM_PROVIDER",
    "ARCHGUARD_S3_BUCKET",
    "ARCHGUARD_TEST_MODE",
}


def _string_constants() -> dict[str, str]:
    """Module-level `NAME = "VALUE"` across the package.

    Collected so `os.environ.get(DATABASE_URL_ENV)` resolves to the variable it
    actually reads instead of being skipped.
    """
    found: dict[str, str] = {}
    for path in PACKAGE.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        found[target.id] = node.value.value
    return found


def _variables_read() -> dict[str, str]:
    """Every environment variable the package reads, to `file:line`."""
    consts = _string_constants()
    reads: dict[str, str] = {}

    def name_of(node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return consts.get(node.id)
        if isinstance(node, ast.Attribute):
            return consts.get(node.attr)
        return None

    for path in PACKAGE.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            target = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"get", "getenv"} and node.args:
                    if "environ" in ast.unparse(node.func.value) or ast.unparse(
                        node.func.value
                    ).endswith("os"):
                        target = name_of(node.args[0])
            elif isinstance(node, ast.Subscript):
                if "environ" in ast.unparse(node.value):
                    target = name_of(node.slice)
            if target:
                reads.setdefault(target, f"{rel}:{node.lineno}")
    return reads


def _documented() -> set[str]:
    """Variables named in `.env.example`, set or commented."""
    text = EXAMPLE.read_text(encoding="utf-8")
    return set(
        re.findall(
            r"\b(ARCHGUARD_[A-Z0-9_]+|GITHUB_[A-Z0-9_]+|GEMINI_[A-Z0-9_]+"
            r"|SENTRY_[A-Z0-9_]+|SESSION_SECRET|DATABASE_URL|TEST_DATABASE_URL"
            r"|REDIS_URL|ALLOWED_ORIGINS|ENVIRONMENT|LOG_LEVEL|OPENAI_API_KEY"
            r"|PLAYWRIGHT_REUSE_SERVER)\b",
            text,
        )
    )


def test_every_variable_the_code_reads_is_documented():
    """An undocumented variable is one an operator cannot know to set.

    `ARCHGUARD_DATA_DIR` was among the missing ones, and the production gate
    refuses to start when the directory it names is not writable -- so the
    deployment fails on a setting no document mentions.
    """
    undocumented = {
        name: where for name, where in sorted(_variables_read().items())
        if name not in _documented()
    }

    assert not undocumented, (
        "read by the code and absent from .env.example:\n  "
        + "\n  ".join(f"{n:38} {w}" for n, w in undocumented.items())
    )


def test_nothing_documented_is_dead():
    """A variable in the template is a promise that setting it does something."""
    read = set(_variables_read())
    dead = sorted(_documented() - read - NON_APPLICATION)

    assert not dead, (
        "documented in .env.example but read nowhere in archguard/: "
        f"{dead}. If one of these is genuinely used by tests or tooling, add it "
        "to NON_APPLICATION with the reason; otherwise remove it."
    )


@pytest.mark.parametrize("name", sorted(KNOWN_DEAD))
def test_a_variable_removed_with_the_cli_has_not_come_back(name: str):
    """Named individually so the failure says which one and why.

    These were documented for months after the code that read them was deleted.
    `ARCHGUARD_SKIP_LLM` in particular reads like the way to run without an LLM,
    and the way to do that is to leave `GEMINI_API_KEY` unset.
    """
    assert name not in EXAMPLE.read_text(encoding="utf-8"), (
        f"{name} is documented again; nothing reads it."
    )


def test_no_variable_is_documented_twice():
    """The observability block appeared four times, and the two truncated
    comments left behind by the duplication were mid-sentence."""
    blocks = re.findall(
        r"^#?\s*([A-Z][A-Z0-9_]{2,})\s+—", EXAMPLE.read_text(encoding="utf-8"), re.M
    )
    repeated = sorted({n for n in blocks if blocks.count(n) > 1})

    assert not repeated, f"documented more than once in .env.example: {repeated}"


def test_the_production_requirements_are_marked_as_required():
    """The gate is the authority on what a deployment must set.

    Everything `_config_check` consults should be findable in the template with
    the word REQUIRED near it, or an operator reads the list as advice.
    """
    check = (PACKAGE / "dashboard" / "_config_check.py").read_text(encoding="utf-8")
    consulted = set(re.findall(r'_get\("([A-Z][A-Z0-9_]+)"\)', check))
    # Not a requirement, only refused when it collides or is enabled.
    optional = {"ARCHGUARD_DASHBOARD_TOKEN", "ARCHGUARD_DASHBOARD_ALLOW_REMOTE"}

    text = EXAMPLE.read_text(encoding="utf-8")
    unmarked = []
    for name in sorted(consulted - optional):
        index = text.find(name)
        if index == -1 or "REQUIRED" not in text[max(0, index - 400) : index + 400]:
            unmarked.append(name)

    assert not unmarked, (
        "the production config check refuses to start without these, and "
        f".env.example does not say so near them: {unmarked}"
    )


# --------------------------------------------------------- the removed CLI


#: Documents whose job is to record what the project used to be. A CLI
#: reference in one of these is history, not drift, and removing it would
#: destroy the reason a decision was made.
HISTORICAL = {
    "docs/PHASE1_SIGNOFF.md",
    "docs/PHASE2_SIGNOFF.md",
    "docs/PHASE3_SIGNOFF.md",
    "docs/PHASE2_BASELINE.md",
    "docs/BASELINE.md",
    "docs/PENDING_VERIFICATION.md",
    "CHANGELOG.md",
}

#: Invocations of a binary that no longer exists. Prose *about* the removal is
#: fine and expected; a command someone could try to run is not.
REMOVED_COMMANDS = [
    "archguard analyze",
    "archguard init",
    "archguard fitness",
    "archguard history",
    "archguard report",
    "archguard sync",
    "archguard suppress",
    "archguard github-sync",
]


def _current_docs() -> list[Path]:
    """Markdown that describes the product as it is now."""
    docs = [ROOT / "README.md", ROOT / "SECURITY.md", ROOT / "CONTRIBUTING.md"]
    docs += sorted((ROOT / "docs").glob("*.md"))
    return [
        d
        for d in docs
        if d.exists() and d.relative_to(ROOT).as_posix() not in HISTORICAL
        # ADRs record decisions in their own time and are left alone.
        and "adr" not in d.relative_to(ROOT).parts
    ]


@pytest.mark.parametrize("command", REMOVED_COMMANDS)
def test_no_current_document_tells_anyone_to_run_the_cli(command: str):
    """The CLI was removed and the documentation was not.

    A release runbook still listed `archguard analyze --repo . --no-llm` as a
    mandatory pre-release gate, and the README opened with a page of commands
    none of which exist -- so the documented first-run path was impossible.

    Historical documents and ADRs are excluded on purpose: they are supposed to
    mention it.
    """
    offenders = [
        f"{d.relative_to(ROOT).as_posix()}:{i}"
        for d in _current_docs()
        for i, line in enumerate(d.read_text(encoding="utf-8").splitlines(), 1)
        if f"`{command}" in line or line.strip().startswith(command)
    ]

    assert not offenders, (
        f"{command!r} is not a command that exists; found at: {offenders}"
    )


def test_the_package_ships_no_console_entry_point():
    """What makes the above true. If an entry point comes back, the docs above
    should be allowed to describe it again, and this test should be the thing
    that says so."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "[tool.poetry.scripts]" not in pyproject
    assert "[project.scripts]" not in pyproject
    assert not (PACKAGE / "cli").exists(), "archguard/cli is back; update the docs"


def test_the_documented_start_commands_are_the_configured_ones():
    """A runbook whose start command does not match the deployment config is a
    runbook that has never been followed."""
    deployment = (ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "uvicorn archguard.dashboard.app:app" in deployment
    assert "arq archguard.worker" in deployment, (
        "the worker's start command is not in the deployment guide"
    )

    for config in ("render.yaml", "railway.toml", "railway.worker.toml"):
        path = ROOT / config
        if path.exists():
            text = path.read_text(encoding="utf-8")
            assert "archguard" in text, f"{config} does not reference the package"


def test_the_health_check_path_matches_the_platform_configs():
    """`/health` is liveness and answers 200 whether or not the process can
    reach PostgreSQL; `/ready` is what the platforms poll. The deploy checklist
    said `/health`, which would have let a broken instance take traffic."""
    for config in ("render.yaml", "railway.toml"):
        path = ROOT / config
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "healthcheck" in text.lower() or "healthCheckPath" in text:
            assert "/ready" in text, (
                f"{config} polls something other than /ready; if that changed "
                "deliberately, update docs/DEPLOYMENT.md's deploy checklist too"
            )
