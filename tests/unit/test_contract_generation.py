"""Headless contract generation (archguard.contract.generation).

Replaces the dashboard's previous route into the CLI, where it built a fake
``typer.Context`` and called ``archguard.cli._init_dispatch._run_init_cli``.
These tests pin the behaviour that had to survive that extraction: real
co-change module detection, honest fallback reporting, and no terminal
dependency anywhere on the path.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from archguard.contract.generation import (
    ContractGenerationError,
    ContractGenerationResult,
    generate_contract,
)

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "T",
    "GIT_AUTHOR_EMAIL": "t@e.x",
    "GIT_COMMITTER_NAME": "T",
    "GIT_COMMITTER_EMAIL": "t@e.x",
    "GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z",
}


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, env=_GIT_ENV
    )


def _build_two_module_repo(root: Path) -> None:
    """A repo whose history contains two distinct co-change clusters.

    api/ and db/ are always committed separately, so Louvain sees two
    communities rather than one blob -- which is what makes this exercise the
    measured path instead of the directory-name fallback.
    """
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    (root / "api").mkdir()
    (root / "db").mkdir()
    for rev in range(4):
        for pkg in ("api", "db"):
            for name in ("alpha", "beta", "gamma"):
                (root / pkg / f"{name}.py").write_text(
                    f"# {pkg}.{name} rev{rev}\nimport os\n\n\ndef f{rev}():\n"
                    f"    return {rev}\n"
                )
        _git(root, "add", "api")
        _git(root, "commit", "-q", "-m", f"api rev{rev}")
        _git(root, "add", "db")
        _git(root, "commit", "-q", "-m", f"db rev{rev}")


@pytest.fixture(scope="module")
def two_module_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    repo = tmp_path_factory.mktemp("cochange") / "repo"
    _build_two_module_repo(repo)
    return repo


@pytest.fixture(scope="module")
def generated(two_module_repo: Path) -> Iterator[ContractGenerationResult]:
    yield generate_contract(
        repo_root=two_module_repo,
        output=two_module_repo / ".archguard.yml",
        threshold_profile="ci",
        min_history_commits=1,
    )


# ---------------------------------------------------------------------------
# The measured path
# ---------------------------------------------------------------------------


def test_detects_modules_from_co_change_history(
    generated: ContractGenerationResult,
) -> None:
    assert generated.module_count == 2
    assert generated.commit_count == 8
    assert generated.files_scanned == 6


def test_measured_boundaries_are_not_reported_as_a_fallback(
    generated: ContractGenerationResult,
) -> None:
    """The single most consequential field: it decides whether the dashboard
    tells the user their module map was measured or guessed."""
    assert generated.fallback_used is False
    assert generated.fallback_reason == ""


def test_writes_a_valid_contract(
    generated: ContractGenerationResult, two_module_repo: Path
) -> None:
    contract = yaml.safe_load((two_module_repo / ".archguard.yml").read_text())
    assert contract["version"] == "3.0"
    assert contract["profile"] == "ci"
    assert {m["name"] for m in contract["modules"]} == {"api", "db"}
    assert {m["path"] for m in contract["modules"]} == {"api/", "db/"}


def test_profile_thresholds_are_fixed_not_self_referential(
    generated: ContractGenerationResult, two_module_repo: Path
) -> None:
    """A contract generated and graded in the same pass must not derive its
    budgets from the repository's own current fan-out, or the repository can
    only ever pass its own first scan."""
    contract = yaml.safe_load((two_module_repo / ".archguard.yml").read_text())
    # "ci" profile: max_coupling 10, min_health_score 75 -> fail_threshold 0.25
    assert all(m["coupling_budget"] == 10 for m in contract["modules"])
    assert contract["fail_threshold"] == pytest.approx(0.25)
    assert contract["warn_threshold"] < contract["fail_threshold"]


def test_cycle_gate_is_added_under_a_profile(
    generated: ContractGenerationResult, two_module_repo: Path
) -> None:
    contract = yaml.safe_load((two_module_repo / ".archguard.yml").read_text())
    rules = {f["rule"] for f in contract["fitness_functions"]}
    assert "graph.cycles == 0" in rules


def test_reports_progress(two_module_repo: Path, tmp_path: Path) -> None:
    messages: list[str] = []
    generate_contract(
        repo_root=two_module_repo,
        output=tmp_path / "out.yml",
        threshold_profile="ci",
        min_history_commits=1,
        on_progress=messages.append,
    )
    assert any("Scanning" in m for m in messages)
    assert any("commits" in m for m in messages)
    assert any("modules" in m for m in messages)


# ---------------------------------------------------------------------------
# The fallback path
# ---------------------------------------------------------------------------


def test_falls_back_when_there_is_no_git_history(tmp_path: Path) -> None:
    """A plain directory has no history to measure, so boundaries are guessed.

    The reason must be reported distinctly: "we could not read this repo's
    history" is not the same as "this repo has little history", and collapsing
    them is how a guessed module map gets presented as a measured one.
    """
    repo = tmp_path / "nogit"
    (repo / "pkg").mkdir(parents=True)
    for name in ("a", "b", "c"):
        (repo / "pkg" / f"{name}.py").write_text("import os\n")

    result = generate_contract(
        repo_root=repo, output=repo / ".archguard.yml", threshold_profile="ci"
    )

    assert result.fallback_used is True
    assert result.fallback_reason == "history_unavailable"


def test_falls_back_when_history_is_shorter_than_required(
    two_module_repo: Path, tmp_path: Path
) -> None:
    result = generate_contract(
        repo_root=two_module_repo,
        output=tmp_path / "sparse.yml",
        threshold_profile="ci",
        min_history_commits=10_000,
    )
    assert result.fallback_used is True
    assert result.fallback_reason == "sparse_history"


def test_fallback_reason_survives_into_the_written_contract(
    tmp_path: Path,
) -> None:
    """dashboard.js matches the reason key to explain the banner to the user."""
    repo = tmp_path / "nogit2"
    (repo / "pkg").mkdir(parents=True)
    for name in ("a", "b", "c"):
        (repo / "pkg" / f"{name}.py").write_text("import os\n")

    generate_contract(repo_root=repo, output=repo / ".archguard.yml")
    contract = yaml.safe_load((repo / ".archguard.yml").read_text())
    assert "fallback" in contract["generated_by"]
    assert "history_unavailable" in contract["generated_by"]


# ---------------------------------------------------------------------------
# Failure and independence
# ---------------------------------------------------------------------------


def test_a_repository_with_no_python_is_an_explicit_error(tmp_path: Path) -> None:
    repo = tmp_path / "empty"
    repo.mkdir()
    (repo / "README.md").write_text("nothing to analyse\n")

    with pytest.raises(ContractGenerationError, match="No Python files"):
        generate_contract(repo_root=repo, output=repo / ".archguard.yml")


def test_generation_does_not_depend_on_the_cli_or_a_terminal() -> None:
    """The whole point of the extraction.

    Importing the generator must not pull in typer or rich: they are the CLI's
    dependencies, they are being removed, and a web request has no terminal to
    print to.
    """
    probe = (
        "import sys; import archguard.contract.generation as g; "
        "bad = [m for m in ('typer', 'rich', 'archguard.cli') "
        "if any(k == m or k.startswith(m + '.') for k in sys.modules)]; "
        "print(','.join(bad))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "", f"generation pulled in: {out.stdout.strip()}"
