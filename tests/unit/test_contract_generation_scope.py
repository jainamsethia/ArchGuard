"""What a generated contract is allowed to call an architectural module.

Measured on four real repositories before this was written, every one of them
came out wrong:

    requests  requests/ (0 files), requests.packages/ (0 files)
    flask     tests/, src/, flask/ (0 files), examples/celery/
    click     tests/, src/, examples/
    httpx     httpx/, tests/

Only httpx had its real package named. `requests` generated a contract covering
*zero* files and the analysis then reported 100.0/PASS -- a silent false pass on
a repository it had not measured at all. Flask, whose generated contract was
dominated by the fan-out of its own test package, came out F/FAIL.

Three separate defects produced that, and this file pins all three:

  phantom       modules inferred from paths that no longer exist. Co-change
                history is read with `--no-renames`, so Flask's 2019 move of
                `flask/` to `src/flask/` reads as two unrelated files and the
                dead path becomes a module covering nothing.
  non-shipping  `tests/`, `examples/`, `docs/` scored as architecture. Test code
                imports broadly by design; a coupling budget on it is a category
                error, and it was the finding that dominated Flask's grade.
  src wrapper   `src/` is a build layout, not a module. The real package is
                `src/flask/`, and naming the wrapper means the actual
                architecture is never named at all.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from archguard.contract.generation import (
    NoAnalysableModuleError,
    generate_contract,
)

pytestmark = pytest.mark.usefixtures()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.email=t@example.invalid",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "-m",
        message,
    )


def _write(repo: Path, rel: str, body: str = "x = 1\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.fixture()
def src_layout_repo(tmp_path: Path) -> Path:
    """A repository shaped like the ones that failed.

    ``src/`` package layout, a root ``tests/`` tree, ``examples/`` and
    ``docs/`` -- and a rename in history, so the co-change graph carries a path
    that no longer exists. That last part is what reproduces the phantom
    module, and a fixture without it would let the bug back in.
    """
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")

    # The package starts life at the repository root, as Flask's did.
    for name in ("__init__", "core", "helpers", "io"):
        _write(repo, f"proj/{name}.py", "import os\n")
    _commit(repo, "initial layout")

    for name in ("core", "helpers"):
        _write(repo, f"proj/{name}.py", "import os\nimport sys\n")
    _commit(repo, "edit in the old layout")

    # ...then moves under src/, exactly the migration that creates the phantom.
    (repo / "src").mkdir()
    subprocess.run(
        ["git", "-C", str(repo), "mv", "proj", "src/proj"],
        check=True,
        capture_output=True,
    )
    _commit(repo, "move package under src/")

    for name in ("core", "helpers", "io"):
        _write(repo, f"src/proj/{name}.py", "import os\nimport sys\nimport json\n")
    _commit(repo, "edit after the move")

    for name in ("test_core", "test_helpers", "conftest"):
        _write(repo, f"tests/{name}.py", "import os\nimport sys\nimport json\n")
    _write(repo, "examples/demo.py")
    _write(repo, "docs/conf.py")
    _commit(repo, "tests, examples and docs")

    for name in ("test_core", "test_helpers"):
        _write(repo, f"tests/{name}.py", "import os\nimport sys\nimport json\nimport re\n")
    _commit(repo, "edit the tests")

    return repo


def _modules(repo: Path, tmp_path: Path) -> list[dict]:
    out = tmp_path / "generated.yml"
    generate_contract(repo_root=repo, output=out, threshold_profile="ci")
    contract = yaml.safe_load(out.read_text(encoding="utf-8"))
    modules: list[dict] = contract["modules"]
    return modules


# ------------------------------------------------------------------ phantoms


def test_no_module_points_at_a_path_that_does_not_exist(src_layout_repo, tmp_path):
    """The defect that made requests report 100.0/PASS on nothing.

    A module whose path matches no file cannot be measured, so emitting it
    produces a module that scores perfectly by having nothing to score.
    """
    for module in _modules(src_layout_repo, tmp_path):
        path = (src_layout_repo / module["path"].rstrip("/")).resolve()
        assert path.exists(), (
            f"module {module['name']!r} points at {module['path']!r}, "
            "which does not exist in the working tree"
        )


def test_every_module_actually_covers_python_files(src_layout_repo, tmp_path):
    """Existing is not enough -- an empty directory is just as unmeasurable."""
    for module in _modules(src_layout_repo, tmp_path):
        directory = src_layout_repo / module["path"].rstrip("/")
        found = list(directory.rglob("*.py")) if directory.is_dir() else []
        assert found, f"module {module['name']!r} covers no Python files"


# -------------------------------------------------------------- non-shipping


@pytest.mark.parametrize("tree", ["tests", "examples", "docs"])
def test_non_shipping_trees_are_not_modules(src_layout_repo, tmp_path, tree):
    """Test code imports broadly by design; a fan-out budget on it is a
    category error, and on Flask it was the finding that produced the F."""
    paths = [m["path"].strip("/") for m in _modules(src_layout_repo, tmp_path)]
    assert not any(
        p == tree or p.startswith(f"{tree}/") for p in paths
    ), f"{tree}/ was generated as an architectural module: {paths}"


def test_the_shipping_package_is_still_found(src_layout_repo, tmp_path):
    """Excluding the rest must not exclude everything."""
    paths = [m["path"].strip("/") for m in _modules(src_layout_repo, tmp_path)]
    assert any("proj" in p for p in paths), f"the package was not found: {paths}"


# --------------------------------------------------------------- src wrapper


def test_the_package_is_named_not_the_src_wrapper(src_layout_repo, tmp_path):
    """`src/` is a build layout. Naming it means the real module never is.

    Flask, click and requests all place their package under `src/`; before this,
    all three generated a module called `src`.
    """
    paths = [m["path"].strip("/") for m in _modules(src_layout_repo, tmp_path)]
    assert "src" not in paths, f"the build wrapper became a module: {paths}"
    assert any(p == "src/proj" for p in paths), (
        f"expected the package at src/proj, got {paths}"
    )


def test_the_module_name_reads_as_the_package(src_layout_repo, tmp_path):
    names = [m["name"] for m in _modules(src_layout_repo, tmp_path)]
    assert "src" not in names
    assert any("proj" in n for n in names), names


# ------------------------------------------------------------- zero coverage


def test_a_repository_with_nothing_shippable_is_refused(tmp_path):
    """Rather than emitting a contract that measures nothing.

    This is the requests case: when every candidate is filtered away there is
    no honest contract to write, and writing one anyway is what produced a
    100.0/PASS on a repository where nothing had been measured.
    """
    repo = tmp_path / "onlytests"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for name in ("test_a", "test_b", "conftest"):
        _write(repo, f"tests/{name}.py", "import os\n")
    _commit(repo, "tests only")
    _write(repo, "tests/test_a.py", "import os\nimport sys\n")
    _commit(repo, "edit")

    with pytest.raises(NoAnalysableModuleError) as exc:
        generate_contract(
            repo_root=repo, output=tmp_path / "out.yml", threshold_profile="ci"
        )
    # The message has to say which problem this is, because "generation failed"
    # sends the reader looking for a crash.
    assert "no" in str(exc.value).lower()


def test_the_refusal_names_what_was_excluded(tmp_path):
    """So the answer is actionable rather than a dead end."""
    repo = tmp_path / "onlydocs"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, "docs/conf.py", "import os\n")
    _write(repo, "docs/build.py", "import os\n")
    _write(repo, "docs/extra.py", "import os\n")
    _commit(repo, "docs only")
    _write(repo, "docs/conf.py", "import os\nimport sys\n")
    _commit(repo, "edit")

    with pytest.raises(NoAnalysableModuleError) as exc:
        generate_contract(
            repo_root=repo, output=tmp_path / "out.yml", threshold_profile="ci"
        )
    message = str(exc.value).lower()
    assert any(word in message for word in ("test", "doc", "example", "shipping")), (
        f"the refusal does not explain itself: {exc.value}"
    )


# --------------------------------------------------------- unresolvable paths


def test_no_module_is_emitted_that_the_scorer_cannot_resolve(
    src_layout_repo, tmp_path
):
    """A generated module must be able to receive at least one file.

    A community whose files span several top-level directories infers the
    common prefix `./`, which `path_belongs_to_module` never matches -- it
    requires the file to start with the prefix, and "tests/x.py" does not start
    with "./". Such a module is emitted, measured against nothing, and scored a
    meaningless 100 that dilutes the repository average. Seen on psf/requests
    as a `root` module covering zero files.
    """
    from archguard.utils.paths import path_belongs_to_module

    modules = _modules(src_layout_repo, tmp_path)
    on_disk = [
        str(f.relative_to(src_layout_repo)).replace("\\", "/")
        for f in src_layout_repo.rglob("*.py")
    ]

    for module in modules:
        matched = [f for f in on_disk if path_belongs_to_module(f, [module["path"]])]
        assert matched, (
            f"module {module['name']!r} at {module['path']!r} can never be "
            "assigned a file by the scorer"
        )


def test_a_dot_slash_path_is_never_emitted(src_layout_repo, tmp_path):
    """The specific shape the above rejects, pinned by name so a future
    refactor of the general check cannot quietly let it back."""
    paths = [m["path"] for m in _modules(src_layout_repo, tmp_path)]
    assert "./" not in paths, paths


# ------------------------------------------------------- the refusal is heard


def test_the_worker_reports_the_refusal_verbatim():
    """A refusal the user never sees is the same as no refusal.

    The adapter wraps contract generation in a catch-all that logs a warning
    and continues "anyway"; the worker then turns anything unrecognised into
    "Analysis failed unexpectedly". Between them, a considered explanation of
    why a repository has no analysable module became a shrug. Both now let
    ContractGenerationError through.
    """
    import inspect

    from archguard.dashboard import pipeline_adapter
    from archguard.worker import tasks

    adapter_src = inspect.getsource(pipeline_adapter.run_analysis_on_repo)
    assert "except NoAnalysableModuleError" in adapter_src, (
        "the adapter still swallows the refusal into 'attempting analysis anyway'"
    )

    worker_src = inspect.getsource(tasks.analyse_repository)
    assert "except NoAnalysableModuleError" in worker_src, (
        "the worker still reports the refusal as an unexpected failure"
    )
    # Ahead of the catch-all, or it never runs.
    assert worker_src.index("except NoAnalysableModuleError") < worker_src.index(
        "except Exception as exc"
    )
