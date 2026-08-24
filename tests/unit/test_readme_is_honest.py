"""The README must not instruct a reader to run something that does not exist.

The CLI was removed, but the README went on documenting it as the product: a
whole `## CLI Commands` section, a Quick Start built on `archguard init`, S3
cache sync via `archguard sync push`, a GitHub Action at `action@v1` whose
directory had been deleted, PR-comment posting with no code behind it, and
`pip install -e ".[all]"` naming an extra that was never defined. Every one of
those instructions failed on the shipped product, on the front page of the
repository.

The rule enforced here is deliberately narrow: prose may *discuss* the removed
CLI -- the FAQ does, to tell readers it is gone -- but a shell block is an
instruction, and an instruction has to work.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
README = (REPO / "README.md").read_text(encoding="utf-8", errors="replace")

#: Fenced blocks a reader would copy and run.
_SHELL_FENCE = re.compile(r"```(?:bash|sh|shell|console)\n(.*?)```", re.S)

#: `pip install -e ".[ml]"`, `poetry install --extras worker`, `.[all]`
_EXTRA = re.compile(r"\.\[([a-z0-9_,\s-]+)\]|--extras[= ]([a-z0-9_-]+)")


#: `run:` steps inside a ```yaml workflow block are instructions too -- the S3
#: cache-sync commands lived there rather than in a shell fence, which is how
#: they survived a check that only looked at shell blocks.
_YAML_FENCE = re.compile(r"```yaml\n(.*?)```", re.S)
_RUN_STEP = re.compile(r"^\s*(?:-\s*)?run:\s*(?:\||>)?\s*(.*)$", re.M)


def _shell_blocks() -> list[str]:
    blocks = _SHELL_FENCE.findall(README)
    for yaml_block in _YAML_FENCE.findall(README):
        blocks.extend(_RUN_STEP.findall(yaml_block))
    return blocks


def _defined_extras() -> set[str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return set(data.get("tool", {}).get("poetry", {}).get("extras", {}))


def test_the_fence_parser_still_finds_shell_blocks() -> None:
    # Guards the regex: if it stops matching, every check below passes
    # vacuously and this file silently stops checking anything.
    assert len(_shell_blocks()) >= 5


def test_no_shell_block_invokes_the_removed_cli() -> None:
    offenders = []
    for block in _shell_blocks():
        for line in block.splitlines():
            stripped = line.strip().lstrip("$ ").strip()
            if re.match(r"^archguard\s+[a-z]", stripped):
                offenders.append(stripped)

    assert not offenders, (
        "The README tells a reader to run a CLI that was removed in f7dfbda:\n  "
        + "\n  ".join(offenders)
        + "\nProse may say the CLI is gone; a shell block is an instruction and "
        "has to work."
    )


@pytest.mark.parametrize("marker", ["archguard sync ", "ArchGuard/action@", "--bucket "])
def test_no_instructions_for_subsystems_that_were_deleted(marker: str) -> None:
    # S3 cache sync and the GitHub Action both had README instructions long
    # after the code and the action/ directory were gone.
    for block in _shell_blocks():
        assert marker not in block, f"{marker!r} names a subsystem that no longer exists"


def test_yaml_blocks_do_not_reference_the_deleted_action() -> None:
    # The Action was documented in a ```yaml workflow block, not a shell one.
    assert "ArchGuard/action@" not in README, (
        "the action/ directory was deleted; a workflow using it cannot resolve"
    )


def test_every_referenced_extra_actually_exists() -> None:
    defined = _defined_extras()
    referenced: set[str] = set()
    for block in _shell_blocks():
        for bracket, flag in _EXTRA.findall(block):
            for name in (bracket or flag or "").split(","):
                name = name.strip()
                if name:
                    referenced.add(name)

    unknown = referenced - defined
    assert not unknown, (
        f"README install commands name extras that pyproject.toml does not "
        f"define: {sorted(unknown)}. Defined: {sorted(defined)}. "
        "`pip install -e \".[all]\"` failed for exactly this reason."
    )
