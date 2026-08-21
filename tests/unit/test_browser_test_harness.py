"""The Playwright harness has to start the server it tests.

`playwright.config.ts` declared `webServer.command: 'uvicorn ...'`. `uvicorn` is
not on PATH unless a virtualenv is already activated, so the command failed:

    [WebServer] 'uvicorn' is not recognized as an internal or external command

Locally that was masked by `reuseExistingServer`, which found whatever happened
to be listening on 8765 and tested that instead. Worse than a plain failure: a
uvicorn from five hours earlier was still bound to the port during the P2-3
asset work, and Playwright tested *it* -- producing a spurious 500 on
/dashboard.html and six spurious accessibility failures that had nothing to do
with the change under review, and which cost two rounds of investigation.

In CI it would not have been masked at all. `reuseExistingServer` is false
there, while the workflow started its own server on the same port in a previous
step -- so Playwright would have refused the occupied port. Neither half had
ever run: these workflows have never executed on GitHub.

These tests read the committed configuration. They cannot prove a browser
launches, but they pin the three properties that were wrong, and they run in
the ordinary suite rather than only when someone happens to run Playwright.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "playwright.config.ts"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def config_text() -> str:
    assert CONFIG.exists(), "playwright.config.ts is missing"
    return CONFIG.read_text(encoding="utf-8")


def _web_server_block(text: str) -> str:
    start = text.index("webServer:")
    return text[start:]


def test_the_server_command_does_not_rely_on_an_activated_virtualenv(config_text):
    """`uvicorn ...` only resolves if someone has already activated a venv."""
    block = _web_server_block(config_text)
    command = re.search(r"command:\s*`([^`]+)`|command:\s*'([^']+)'", block)
    assert command, "webServer has no command"
    text = command.group(1) or command.group(2)
    assert not text.strip().startswith("uvicorn"), (
        "the command still invokes bare `uvicorn`, which is not on PATH "
        "without an activated virtualenv"
    )


def test_the_server_command_runs_uvicorn_through_an_interpreter(config_text):
    """`python -m uvicorn` works wherever the interpreter is resolvable, which
    a console script installed into a venv's bin directory does not."""
    block = _web_server_block(config_text)
    assert "-m uvicorn" in block, "the command does not run uvicorn as a module"


def test_the_interpreter_is_resolved_rather_than_assumed(config_text):
    """A bare `python` is the wrong interpreter as often as the right one: it
    is whichever comes first on PATH, not the one holding the dependencies."""
    assert "existsSync" in config_text, (
        "nothing detects the project virtualenv; the command assumes an "
        "interpreter rather than locating one"
    )
    assert ".venv" in config_text


def test_an_explicit_interpreter_can_be_supplied(config_text):
    """An escape hatch for an environment the detection does not anticipate --
    a container, a CI image, a virtualenv somewhere else."""
    assert "ARCHGUARD_PYTHON" in config_text


def test_reusing_a_stranger_server_is_opt_in(config_text):
    """Silently testing whatever is on the port is how a five-hour-old process
    produced three false regressions. Off unless someone asks for it."""
    block = _web_server_block(config_text)
    match = re.search(r"reuseExistingServer:\s*([^,\n]+)", block)
    assert match, "reuseExistingServer is not set at all, so it defaults to on"
    value = match.group(1).strip()
    assert value != "true", "the harness always reuses whatever is listening"
    assert "!process.env.CI" not in value, (
        "reuse is still on for every local run, which is where the stale "
        "server problem actually bit"
    )


# ------------------------------------------------------------------ workflow


@pytest.fixture(scope="module")
def workflow_text() -> str:
    assert WORKFLOW.exists(), "ci.yml is missing"
    return WORKFLOW.read_text(encoding="utf-8")


def test_ci_and_the_config_agree_about_who_owns_the_server(workflow_text):
    """Two owners of one port used to disagree.

    The workflow started uvicorn on 8765 in its own step while
    `reuseExistingServer` was false -- which tells Playwright to refuse a port
    already in use. Whichever way it is resolved, the two have to agree: a job
    that starts its own server must also opt into reuse.
    """
    starts_own = "poetry run uvicorn" in workflow_text
    opts_in = "PLAYWRIGHT_REUSE_SERVER" in workflow_text
    assert starts_own == opts_in, (
        "ci.yml starts its own server without opting into reuse (or the "
        "reverse); Playwright will refuse the occupied port"
    )


def test_every_browser_job_that_runs_playwright_opts_into_reuse(workflow_text):
    """Each job starts its own server, so each must say so.

    Reuse is safe here precisely because the server is created in the same job
    moments earlier -- it cannot be a stale process from another run.
    """
    playwright_steps = workflow_text.count("npx playwright test")
    opt_ins = workflow_text.count("PLAYWRIGHT_REUSE_SERVER")
    assert playwright_steps == opt_ins, (
        f"{playwright_steps} playwright steps but {opt_ins} reuse opt-ins"
    )


def test_ci_makes_the_project_virtualenv_discoverable(workflow_text):
    """The same detection has to work in CI as locally, or the config is
    correct in one place and a guess in the other."""
    assert "virtualenvs.in-project" in workflow_text, (
        "poetry installs into a cache directory, so .venv never exists in CI "
        "and the interpreter detection silently falls through"
    )
