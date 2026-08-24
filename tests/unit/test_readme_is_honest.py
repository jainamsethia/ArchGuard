"""No document may instruct a reader to run something that does not exist.

The CLI was removed, but the README went on documenting it as the product: a
whole `## CLI Commands` section, a Quick Start built on `archguard init`, S3
cache sync via `archguard sync push`, a GitHub Action at `action@v1` whose
directory had been deleted, PR-comment posting with no code behind it, and
`pip install -e ".[all]"` naming an extra that was never defined. Every one of
those instructions failed on the shipped product, on the front page of the
repository.

The same rot was later found in docs/DEPLOYMENT.md, whose release checklist
told an operator to verify a deploy with `archguard analyze`, and in
CONTRIBUTING.md, whose first command installed extras that were never defined.
So the check covers every prose document, not only the front page.

The rule is deliberately narrow: prose may *discuss* the removed CLI -- the
README's FAQ does, to say it is gone -- but a shell block is an instruction, and
an instruction has to work.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
#: Every prose document a reader might follow, not just the front page. The
#: README was corrected first and the same rot was then found in
#: docs/DEPLOYMENT.md -- whose release checklist told an operator to verify a
#: deploy with `archguard analyze` -- and in CONTRIBUTING.md, whose very first
#: command installed extras that do not exist. A guard on one file is a guard
#: on the file somebody happens to be looking at.
DOC_PATHS = [
    REPO / "README.md",
    REPO / "CONTRIBUTING.md",
    REPO / "docs" / "DEPLOYMENT.md",
    REPO / "docs" / "DEVELOPMENT.md",
]

DOCS = "\n".join(
    p.read_text(encoding="utf-8", errors="replace") for p in DOC_PATHS if p.is_file()
)

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
    blocks = _SHELL_FENCE.findall(DOCS)
    for yaml_block in _YAML_FENCE.findall(DOCS):
        blocks.extend(_RUN_STEP.findall(yaml_block))
    return blocks


def _defined_extras() -> set[str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return set(data.get("tool", {}).get("poetry", {}).get("extras", {}))


def test_all_documents_were_found() -> None:
    missing = [p.name for p in DOC_PATHS if not p.is_file()]
    assert not missing, f"documents named but not present: {missing}"


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


#: An inline command inside a checklist item, or after "Run", is a directive.
#: Distinguished from prose that merely names the CLI: the README's FAQ says
#: `archguard analyze` no longer exists, which is the opposite of telling
#: somebody to run it. Without this, the worst instance in the repository was
#: invisible -- DEPLOYMENT.md's release checklist asked an operator to verify a
#: deploy with `archguard analyze --repo .`, in a markdown checkbox rather than
#: a shell fence, so a fence-only check sailed past it.
_DIRECTIVE = re.compile(
    r"(?:^\s*[-*]\s*\[[ x]\].*?|Run\s+)`(archguard\s+[a-z][^`]*)`",
    re.M | re.I,
)


def test_no_checklist_or_instruction_names_the_removed_cli() -> None:
    offenders = _DIRECTIVE.findall(DOCS)
    assert not offenders, (
        "A checklist item or instruction tells a reader to run the CLI removed "
        "in f7dfbda:\n  " + "\n  ".join(offenders)
    )


def test_yaml_blocks_do_not_reference_the_deleted_action() -> None:
    # The Action was documented in a ```yaml workflow block, not a shell one.
    assert "ArchGuard/action@" not in DOCS, (
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
        f"An install command names extras that pyproject.toml does not "
        f"define: {sorted(unknown)}. Defined: {sorted(defined)}. "
        "`pip install -e \".[all]\"` failed for exactly this reason."
    )
