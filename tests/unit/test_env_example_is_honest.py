"""Every environment variable `.env.example` documents must be read by something.

A documented setting that no code reads is worse than an undocumented one. The
operator configures it, gets no error, and believes a control is in force that
is not. This project has shipped that bug at least three times:

  * C6 -- the parameter meant to control L4 LLM explanations was read by
    nothing, so the feature it gated had never once run.
  * ``ARCHGUARD_SKIP_LLM`` -- documented as "skip all LLM calls", wired to
    nothing. An instance set it to hold down spend and went on calling.
  * ``ARCHGUARD_SLACK_WEBHOOK`` -- documented as the destination for
    regression alerts, read by nothing, with ``send_slack_alert()`` having no
    caller outside the tests.

Variables that are deliberately not wired yet are allowed, but they have to say
so in their own comment block -- which is the difference between groundwork and
a false promise.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: A block carrying one of these is declaring itself unimplemented on purpose.
UNWIRED_MARKERS = ("NOT IMPLEMENTED", "NOT WIRED")

#: Where a variable could legitimately be read from.
#:
#: Deliberately excludes tests/. A variable only the test suite ever sets is
#: still dead everywhere it matters, and including tests would also let this
#: file's own docstring -- which names the offenders -- count as a reader.
SOURCE_GLOBS = (
    "archguard/**/*.py",
    "archguard/**/*.js",
    "archguard/**/*.html",
    "scripts/*.py",
    "scripts/*.sh",
    ".github/workflows/*.yml",
    "docker-compose.yml",
    "docker-entrypoint.sh",
    "Dockerfile",
    "railway.toml",
    "render.yaml",
    "playwright.config.ts",
    "Makefile",
)

_VAR = re.compile(r"^#?\s*([A-Z][A-Z0-9_]{3,})=", re.M)


def _blocks() -> list[str]:
    """`.env.example` split into comment blocks, one per setting."""
    text = (REPO / ".env.example").read_text(encoding="utf-8", errors="replace")
    return re.split(r"\n\s*\n", text)


def _documented() -> dict[str, str]:
    """Documented variable name -> the block that documents it."""
    found: dict[str, str] = {}
    for block in _blocks():
        for name in _VAR.findall(block):
            # First block wins; a variable is introduced once and may be
            # referenced later in prose.
            found.setdefault(name, block)
    return found


def _source_text() -> str:
    parts = []
    for pattern in SOURCE_GLOBS:
        for path in REPO.glob(pattern):
            if path.is_file():
                try:
                    parts.append(path.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    pass
    return "\n".join(parts)


DOCUMENTED = _documented()
SOURCE = _source_text()


def test_the_example_file_documents_something() -> None:
    # Guards the parser: if this regex stops matching, every test below passes
    # vacuously and the check silently stops checking.
    assert len(DOCUMENTED) > 20, f"only parsed {len(DOCUMENTED)} vars from .env.example"


@pytest.mark.parametrize("name", sorted(DOCUMENTED))
def test_documented_variable_is_read_or_declared_unwired(name: str) -> None:
    if name in SOURCE:
        return

    block = DOCUMENTED[name]
    if any(marker in block for marker in UNWIRED_MARKERS):
        return

    pytest.fail(
        f"{name} is documented in .env.example but no source file reads it. "
        "Setting it therefore does nothing, silently. Either wire it up, or "
        f"mark its comment block {' / '.join(UNWIRED_MARKERS)} so an operator "
        "is not misled into relying on it."
    )


@pytest.mark.parametrize("name", sorted(DOCUMENTED))
def test_unwired_marker_is_not_left_on_a_variable_that_now_works(name: str) -> None:
    block = DOCUMENTED[name]
    if not any(marker in block for marker in UNWIRED_MARKERS):
        return
    # The marker is an admission, not a permanent exemption. Once the code
    # reads the variable, the warning has to come off -- otherwise it decays
    # into the same misinformation from the other direction.
    assert name not in SOURCE, (
        f"{name} is marked as not implemented, but source code now reads it. "
        "Remove the marker from its .env.example block."
    )
