"""What `./` means as a contract module path.

It meant nothing. ``path_belongs_to_module`` normalises ``"./"`` to ``"."``,
appends a slash to get ``"./"``, then requires ``file.startswith("./")`` -- and
repo-relative paths are stored without a leading ``./``, so no file has ever
matched. A module declared at ``./`` received no files, in every layer:
``_assign_file_to_module`` and ``_get_affected_modules`` both went through the
same matcher.

The consequences were quiet. Two shipped fixtures declare ``./``; both assigned
zero files, and `test_planted_duplication_intra_module_no_cross_module_violation`
passed because nothing was in any module rather than because the duplication
was intra-module, which is what its docstring claimed. A flat repository -- every
.py at the top level -- infers ``./`` from contract generation and was scored
against nothing at all, the same false-pass shape as the generated-contract bug
fixed in 4575ad4.

The agreed meaning: **`./` is every file that ships.** Non-shipping trees are
excluded from it, matching what generated contracts already exclude, so a flat
repository's fan-out counts its real dependencies rather than pytest and mock.
An explicit path keeps meaning exactly what it says -- a hand-written ``tests``
module still receives test files, because its author asked for that by name.
"""

from __future__ import annotations

import pytest

from archguard.utils.paths import path_belongs_to_module

WHOLE_REPO = ["./"]


# --------------------------------------------------- the wildcard matches code


@pytest.mark.parametrize(
    "path",
    [
        "main.py",
        "app.py",
        "pkg/core.py",
        "src/proj/deep/nested/mod.py",
    ],
)
def test_the_whole_repo_path_matches_shipping_files(path):
    """The defect, directly. Every one of these returned False."""
    assert path_belongs_to_module(path, WHOLE_REPO) is True


@pytest.mark.parametrize("spelling", ["./", ".", ""])
def test_every_spelling_of_the_repo_root_behaves_the_same(spelling):
    """`./` is what generation writes and what the fixtures use; `.` and the
    empty string are what a hand-written contract may plausibly contain, and
    all three normalise to the same thing."""
    assert path_belongs_to_module("main.py", [spelling]) is True


# ------------------------------------------------ but not non-shipping trees


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_main.py",
        "tests/conftest.py",
        "docs/conf.py",
        "examples/demo.py",
        "scripts/release.py",
    ],
)
def test_the_whole_repo_path_excludes_non_shipping_trees(path):
    """A flat repository's single module must not have its fan-out inflated by
    pytest, sphinx and friends. This is the same exclusion generated contracts
    apply, so the two cannot disagree about what counts as architecture."""
    assert path_belongs_to_module(path, WHOLE_REPO) is False


def test_nested_test_directories_are_excluded_too():
    """Consistent with the existing skip in `_assign_file_to_module`, which
    already refuses `/tests/` at any depth."""
    assert path_belongs_to_module("src/proj/tests/test_x.py", WHOLE_REPO) is False


# ------------------------------------- an explicit path still means what it says


def test_an_explicit_tests_path_still_matches_test_files():
    """The exclusion applies to the `./` wildcard, not to a path an author
    wrote down. ArchGuard's own .archguard.yml declares a `tests` module and
    tracks its coupling deliberately; that must keep working."""
    assert path_belongs_to_module("tests/test_main.py", ["tests/"]) is True
    assert path_belongs_to_module("tests/conftest.py", ["tests"]) is True


def test_an_explicit_docs_path_still_matches():
    assert path_belongs_to_module("docs/conf.py", ["docs/"]) is True


def test_prefix_boundaries_are_unchanged():
    """The property that stops `src/payments/` matching `src/payments_v2/`."""
    assert path_belongs_to_module("src/payments/api.py", ["src/payments/"]) is True
    assert path_belongs_to_module("src/payments_v2/api.py", ["src/payments/"]) is False


# --------------------------------------------------------------- in the layers


def test_a_flat_repository_assigns_its_files():
    """The user-visible case: a small project with every .py at the top level.

    Generation emits `./` for these (kept as the sole module by
    `drop_unresolvable_modules`), and before this they were scored against zero
    files -- a confident grade derived from nothing.
    """
    from archguard.analysis.coupling import _assign_file_to_module

    module_paths = {"misc": ["./"]}
    for name in ("main.py", "utils.py", "models.py"):
        assert _assign_file_to_module(name, module_paths) == "misc", name


def test_a_flat_repository_does_not_absorb_its_own_tests():
    """The risk named when this was agreed: a `./` module in a repo that also
    has a tests/ tree must not start measuring test coupling."""
    from archguard.analysis.coupling import _assign_file_to_module

    module_paths = {"misc": ["./"]}
    assert _assign_file_to_module("main.py", module_paths) == "misc"
    assert _assign_file_to_module("tests/test_main.py", module_paths) is None


def test_the_module_file_map_agrees_with_the_matcher(tmp_path):
    """Layers 3 and 4 build their file sets through `_get_affected_modules`.

    It uses the same matcher, so it was blind to `./` in the same way -- which
    is why no layer disagreed, and why nothing looked broken.
    """
    from archguard.analysis._orchestrator_utils import _get_affected_modules

    (tmp_path / "module_a").mkdir()
    (tmp_path / "module_b").mkdir()
    (tmp_path / "module_a" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "module_b" / "b.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("x = 1\n", encoding="utf-8")

    contract = {"modules": [{"name": "misc", "path": "./"}]}
    files = sorted(tmp_path.rglob("*.py"))
    affected = _get_affected_modules(tmp_path, contract, files)

    assert "misc" in affected, "the whole-repo module received no files"
    names = sorted(p.name for p in affected["misc"])
    assert names == ["a.py", "b.py"], f"expected the two source files, got {names}"


def test_the_shipped_fixture_now_assigns_its_files():
    """`tests/fixtures/planted_duplication` declares a single `misc` module at
    `./` and contained two files that were never assigned to it."""
    from pathlib import Path

    import yaml

    from archguard.analysis._orchestrator_utils import _get_affected_modules

    repo = Path(__file__).resolve().parents[1] / "fixtures" / "planted_duplication"
    contract = yaml.safe_load((repo / ".archguard.yml").read_text(encoding="utf-8"))
    files = sorted(repo.rglob("*.py"))
    affected = _get_affected_modules(repo, contract, files)

    assert affected.get("misc"), "the fixture's whole-repo module is still empty"
    assert len(affected["misc"]) == len(files)


# ------------------------------------------- the interaction with generation


def test_a_whole_repo_module_is_not_kept_beside_real_ones():
    """`./` alongside real modules is a junk drawer, not a description.

    This is the interaction that broke when `./` started matching files.
    `drop_unresolvable_modules` had been discarding the junk drawer by asking
    whether the matcher could resolve it -- which was true only because `./`
    resolved to nothing. The moment it began matching, psf/requests grew its
    `root=./` module straight back, sitting beside `src/requests/` and
    absorbing everything that module did not. The rule is by shape now.
    """
    from archguard.contract._discovery import drop_unresolvable_modules

    kept = drop_unresolvable_modules(
        {
            "requests": ["src/requests/api.py", "src/requests/models.py"],
            "root": ["setup.py", "src/requests/api.py"],
        }
    )
    assert set(kept) == {"requests"}, f"the junk drawer survived: {sorted(kept)}"


def test_a_whole_repo_module_is_kept_when_it_is_the_only_one():
    """A flat repository. Refusing it would turn away small projects that
    analyse perfectly well, and `./` is the honest description of them."""
    from archguard.contract._discovery import drop_unresolvable_modules

    kept = drop_unresolvable_modules({"root": ["main.py", "utils.py", "models.py"]})
    assert set(kept) == {"root"}
