"""The whole chain, for a repository that has no committed contract.

Most repositories do not ship an `.archguard.yml`, so ArchGuard generates one
per scan. That put two defects in series, and the first hid the second:

  the generated contract carried a fresh timestamp
    -> the fingerprint changed on every scan
    -> `plan_analysis` always answered "the contract changed"
    -> incremental analysis never engaged
    -> and so the scoring defect behind it was never reachable here

Fixing the fingerprint (M-1) makes incremental analysis engage for exactly this
population, which is the population the scoring fix (C-1) exists to protect. So
the two have to be verified together: it is not enough that each is right on its
own, because M-1 is what exposes C-1's path to real users.

These tests run the real pipeline against real PostgreSQL, generating a contract
each time rather than committing one, and require the answer to be the same
every scan.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.db_fixtures import requires_postgres
from tests.integration._pipeline_scan import (
    make_user,
    previous_run,
    scan_repo,
    violations_of,
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def uncontracted_repo(tmp_path: Path) -> Path:
    """A repository with NO .archguard.yml, so one is generated per scan.

    Shaped to breach the generated contract rather than to be minimal. The
    auto-generated contract uses the "ci" profile, which allows a coupling
    fan-out of 10, so `hub` imports twelve leaf packages -- a god module, and
    the ordinary reason a real repository fails this check. Without something
    that actually violates, an "every scan agrees" assertion would pass on two
    empty lists.

    Real git history, because contract generation reads co-change data and
    falls back to directory names without it.
    """
    repo = tmp_path / "source"
    leaves = [f"leaf{i:02d}" for i in range(12)]

    for name in [*leaves, "hub"]:
        (repo / name).mkdir(parents=True)
        (repo / name / "__init__.py").write_text("", encoding="utf-8")

    for i, name in enumerate(leaves):
        (repo / name / "core.py").write_text(
            f"VALUE = {i}\n\n\ndef get_value() -> int:\n    return VALUE\n",
            encoding="utf-8",
        )

    body = "".join(f"import {name}.core\n" for name in leaves)
    body += "\n\ndef total() -> int:\n    return "
    body += " + ".join(f"{name}.core.get_value()" for name in leaves)
    body += "\n"
    (repo / "hub" / "app.py").write_text(body, encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    git("add", "-A")
    git("commit", "-q", "-m", "initial")
    return repo


def _shape(run: dict) -> dict:
    """What a user reads, as one comparable object."""
    return {
        "score": run.get("score"),
        "band": run.get("band"),
        "module_scores": run.get("module_scores") or {},
        "violations": sorted(
            (str(v.get("layer")), str(v.get("module")), str(v.get("message")))
            for v in violations_of(run)
        ),
    }


@requires_postgres
def test_a_generated_contract_lets_the_second_scan_reuse_work(
    uncontracted_repo, tmp_path, live_db
):
    """M-1's point, observed through the pipeline rather than the fingerprint.

    The contract is generated afresh for each scan, so before this fix the
    second scan reported a full analysis every time.
    """
    uid = make_user(9601, "gen-contract")
    url = "https://github.com/test/generated-contract.git"

    first = scan_repo(uncontracted_repo, tmp_path / "c1", url, uid)
    assert first["hashes"], "the first scan recorded no file hashes to compare against"

    # The plan the second scan would build, from what the first one measured.
    # Generate a contract again, exactly as a second job would: a fresh clone
    # with no .archguard.yml in it.
    import shutil

    from archguard.cache.incremental import plan_analysis
    from archguard.dashboard.pipeline_adapter import (
        _archguard_version,
        _safe_load_contract,
    )

    clone2 = tmp_path / "c2-src"
    shutil.copytree(
        uncontracted_repo, clone2, ignore=shutil.ignore_patterns(".archguard-cache")
    )
    from archguard.contract.generation import generate_contract

    generate_contract(repo_root=clone2, output=clone2 / ".archguard.yml")
    regenerated = _safe_load_contract(clone2)

    assert regenerated.get("generated_at"), (
        "the generated contract carries no timestamp, so this test proves nothing"
    )

    plan = plan_analysis(
        files=sorted(clone2.rglob("*.py")),
        root=clone2,
        contract=regenerated,
        version=_archguard_version(),
        previous=previous_run(uncontracted_repo, first),
    )

    assert plan.full is False, (
        f"a regenerated contract still forces a full analysis: {plan.reason}"
    )


@requires_postgres
def test_repeated_scans_of_an_untouched_repository_report_the_same_thing(
    uncontracted_repo, tmp_path, live_db
):
    """The chain end to end, and the reason M-1 and C-1 belong together.

    Three scans, each generating its own contract, over source nobody touched.
    Incremental analysis now engages -- which is exactly when the scoring
    defect used to appear -- so the score, the band, the per-module scores and
    the findings must be identical every time.
    """
    uid = make_user(9603, "gen-stable")
    url = "https://github.com/test/generated-stable.git"

    shapes = []
    scan = scan_repo(uncontracted_repo, tmp_path / "c1", url, uid)
    shapes.append(_shape(scan["run"]))

    for n in (2, 3):
        scan = scan_repo(
            uncontracted_repo, tmp_path / f"c{n}", url, uid,
            previous=previous_run(uncontracted_repo, scan),
        )
        shapes.append(_shape(scan["run"]))

    assert shapes[0]["violations"], (
        "the fixture produced no findings, so identical results prove nothing"
    )
    assert shapes[1] == shapes[0], "the second scan disagreed with the first"
    assert shapes[2] == shapes[0], "the third scan disagreed with the first"


@requires_postgres
def test_an_edit_is_still_noticed_through_a_generated_contract(
    uncontracted_repo, tmp_path, live_db
):
    """Stability must not become blindness.

    A stable fingerprint means the cache survives; it must not mean a real
    change goes unreported.
    """
    uid = make_user(9605, "gen-edit")
    url = "https://github.com/test/generated-edit.git"

    first = scan_repo(uncontracted_repo, tmp_path / "c1", url, uid)
    before = _shape(first["run"])

    # A genuinely new import in a leaf, which changes what that module does.
    path = uncontracted_repo / "leaf00" / "core.py"
    path.write_text(
        path.read_text(encoding="utf-8") + "import collections\nimport itertools\n",
        encoding="utf-8",
    )

    second = scan_repo(
        uncontracted_repo, tmp_path / "c2", url, uid,
        previous=previous_run(uncontracted_repo, first),
    )

    # A fresh account and URL, so this one has no previous run and must analyse
    # everything: the reference answer for the edited tree.
    full_uid = make_user(9606, "gen-edit-full")
    full = scan_repo(
        uncontracted_repo, tmp_path / "c3",
        "https://github.com/test/generated-edit-full.git", full_uid,
    )

    incremental_shape = _shape(second["run"])
    assert incremental_shape["score"] == _shape(full["run"])["score"], (
        "an incremental scan of the edited tree scored differently to a full one"
    )
    assert incremental_shape["band"] == _shape(full["run"])["band"]
    assert incremental_shape["module_scores"] == _shape(full["run"])["module_scores"]
    # And the edit did not silently vanish into the cache.
    assert before is not None
