"""Regression tests for the thresholds the dashboard grades a repo against.

Background: ``archguard init`` sets each module's ``coupling_budget`` to
``ceil(fan_out * 1.5)`` of the fan-out measured during that same run. For a team
locking in "don't get worse than today" that is the right policy, and it stays
the default.

The dashboard, though, generates a contract and grades against it in a single
pass, for a repository nobody will enforce it on. Under the self-referential
baseline the budget is by construction always above the measured fan-out, so no
repository could fail its own first scan however badly coupled it was -- every
dashboard run came back 100/A. The dashboard now pins fixed profile thresholds.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
import yaml

from archguard.dashboard.pipeline_adapter import (
    DASHBOARD_THRESHOLD_PROFILE,
    run_analysis_on_repo,
)
from archguard.profiles.defaults import PROFILES

# Third-party roots only -- stdlib imports are excluded from fan-out, so they
# would quietly weaken the fixture. 30 of them puts the module far enough past
# the ci profile's max_coupling of 10 to saturate the coupling delta, rather
# than landing on the grade-A boundary where the test proves little.
_EXTERNAL_IMPORTS = [
    "yaml", "requests", "httpx", "numpy", "pandas", "click", "rich",
    "pydantic", "sqlalchemy", "redis", "boto3", "jinja2", "flask", "django",
    "celery", "kafka", "pymongo", "elasticsearch", "grpc", "protobuf",
    "tensorflow", "torch", "sklearn", "scipy", "matplotlib", "seaborn",
    "bs4", "lxml", "paramiko", "cryptography",
]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


@pytest.fixture(scope="module")
def tangled_repo(tmp_path_factory) -> Path:
    """A repo whose `svc` package has a deliberately excessive fan-out."""
    repo = tmp_path_factory.mktemp("tangled") / "repo"
    (repo / "svc").mkdir(parents=True)
    (repo / "lib").mkdir(parents=True)

    # One module importing 15 distinct top-level roots -> fan_out 15 > 10.
    (repo / "svc" / "handlers.py").write_text(
        "\n".join(f"import {m}" for m in _EXTERNAL_IMPORTS)
        + "\n\n\ndef handle():\n    return 1\n",
        encoding="utf-8",
    )
    (repo / "svc" / "routes.py").write_text(
        "import flask\nimport requests\n\n\ndef route():\n    return 2\n", encoding="utf-8"
    )
    (repo / "svc" / "__init__.py").write_text("", encoding="utf-8")

    for n in ("alpha", "beta", "gamma"):
        (repo / "lib" / f"{n}.py").write_text(
            f"def {n}():\n    return '{n}'\n", encoding="utf-8"
        )
    (repo / "lib" / "__init__.py").write_text("", encoding="utf-8")

    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")

    # A few commits, each touching one package, so co-change detection has
    # something to separate rather than silently using the folder heuristic.
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "initial")
    for i in range(3):
        (repo / "svc" / "handlers.py").write_text(
            "\n".join(f"import {m}" for m in _EXTERNAL_IMPORTS)
            + f"\n\n\ndef handle():\n    return {i}\n",
            encoding="utf-8",
        )
        (repo / "svc" / "routes.py").write_text(
            f"import flask\nimport requests\n\n\ndef route():\n    return {i}\n",
            encoding="utf-8",
        )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", f"svc change {i}")
        for n in ("alpha", "beta"):
            (repo / "lib" / f"{n}.py").write_text(
                f"def {n}():\n    return '{n}{i}'\n", encoding="utf-8"
            )
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", f"lib change {i}")
    return repo


@pytest.fixture(scope="module")
def tangled_analysis(tangled_repo: Path, _schema_at_head):
    """Analyse the tangled repo once and keep both the result and its stored run.

    Module-scoped because the analysis is the expensive part; the run is read
    back here rather than in each test so the round trip through PostgreSQL is
    exercised exactly once.
    """
    import os

    os.environ["DATABASE_URL"] = _schema_at_head
    os.environ["ARCHGUARD_DB_POOL_SIZE"] = "0"

    async def _go():
        from archguard.db import store
        from archguard.db.session import session_scope

        async with session_scope() as session:
            job_id = (await store.create_job(session, "local://tangled")).id
        result = await run_analysis_on_repo(
            tangled_repo, job_id=job_id, repo_url="local://tangled"
        )
        async with session_scope() as session:
            return result, await store.get_latest_run(session, job_id)

    return asyncio.run(_go())


@pytest.fixture(scope="module")
def tangled_result(tangled_analysis):
    return tangled_analysis[0]


@pytest.fixture(scope="module")
def tangled_run(tangled_analysis):
    return tangled_analysis[1]


@pytest.mark.integration
def test_dashboard_contract_uses_fixed_profile_thresholds(tangled_repo, tangled_result):
    """Budgets must be policy, not a restatement of this repo's own fan-out."""
    contract = yaml.safe_load((tangled_repo / ".archguard.yml").read_text(encoding="utf-8"))
    expected = PROFILES[DASHBOARD_THRESHOLD_PROFILE]["thresholds"]["max_coupling"]

    assert contract["profile"] == DASHBOARD_THRESHOLD_PROFILE
    for module in contract["modules"]:
        assert module["coupling_budget"] == expected, (
            f"module {module['name']} was graded against a budget derived from its "
            "own measured fan-out instead of fixed policy"
        )

    # warn must stay below fail, or WARN-band runs read as passes.
    assert contract["warn_threshold"] < contract["fail_threshold"]


@pytest.mark.integration
def test_recorded_fan_out_matches_what_layer2_grades(tangled_repo, tangled_result):
    """``fan_out_at_init`` must be the number the module is actually graded on.

    It used to be measured against the parent directories of each community's
    files while Layer 2 graded against the contract's single inferred ``path``,
    so the two disagreed by 2-3x on real repositories (httpie recorded 21,
    graded on 11). The recorded value is shown to users as evidence, so it has
    to be the value that was used.
    """
    from archguard.analysis._orchestrator_utils import _get_module_paths
    from archguard.analysis.coupling import compute_fan_out
    from archguard.analysis.parser import ImportParser

    contract = yaml.safe_load((tangled_repo / ".archguard.yml").read_text(encoding="utf-8"))
    module_paths = {m["name"]: _get_module_paths(m) for m in contract["modules"]}
    edges = ImportParser().parse_repo(tangled_repo, module_paths, allow_partial=True).edges

    for module in contract["modules"]:
        graded = compute_fan_out(edges, module["name"], module_paths)
        assert module["fan_out_at_init"] == graded, (
            f"module {module['name']}: contract records fan_out_at_init="
            f"{module['fan_out_at_init']} but Layer 2 grades it on {graded}"
        )


@pytest.mark.integration
def test_dashboard_contract_checks_for_dependency_cycles(tangled_repo, tangled_run):
    """A cycle is the one wrong-direction-import signal needing no human policy.

    Auto-generated contracts declare no ``disallowed_imports`` -- deliberately,
    since a cycle shows some edge is wrong but not which one -- so this rule is
    the only Layer-1-adjacent signal available on the dashboard path.
    """
    contract = yaml.safe_load((tangled_repo / ".archguard.yml").read_text(encoding="utf-8"))
    rules = [f["rule"] for f in contract.get("fitness_functions", [])]

    assert "graph.cycles == 0" in rules

    # Never guess which edge of a cycle is the offending one.
    for module in contract["modules"]:
        assert "disallowed_imports" not in module

    # The evaluated outcome must reach storage, not just be computed: the
    # Fitness panel reads it back from the persisted run.
    assert tangled_run is not None, "analysis completed but no run was persisted"
    evaluated = {
        f["rule"] for f in (tangled_run.get("metrics") or {}).get("fitness_results", [])
    }
    assert "graph.cycles == 0" in evaluated, (
        "cycle check was declared in the contract but its result never reached "
        f"the stored run: {sorted(evaluated)}"
    )


@pytest.mark.integration
def test_badly_coupled_repo_does_not_score_perfect(tangled_result):
    """The actual regression: a tangled repo used to score 100/A on first scan."""
    assert tangled_result.error is None
    assert tangled_result.skipped is False

    assert tangled_result.health_score < 100.0, (
        "a module with fan-out 15 against a budget of 10 scored a perfect 100 -- "
        "the contract is being graded against itself again"
    )
    assert tangled_result.health_grade != "A"
    assert tangled_result.total_violations > 0


@pytest.mark.integration
def test_excessive_fan_out_is_reported_as_a_violation(tangled_result):
    layer2 = [lr for lr in tangled_result.layer_results if lr.layer == 2]
    assert layer2 and layer2[0].violation_count > 0, (
        "Layer 2 (coupling) recorded no violation despite an over-budget module"
    )


@pytest.mark.integration
def test_cli_init_path_keeps_its_own_policy(tangled_repo, tmp_path):
    """``archguard init`` must not inherit any of the dashboard's policy.

    The dashboard pins fixed thresholds and declares a cycle check because it
    grades a repo nobody will enforce the contract against. Neither belongs on
    the CLI path, where the contract is a baseline the team will enforce on
    themselves and the thresholds are theirs to choose.
    """
    import math

    from archguard.contract.generation import generate_contract

    out = tmp_path / "baseline.archguard.yml"
    generate_contract(
        repo_root=tangled_repo,
        output=out,
        min_history_commits=1,
        threshold_profile=None,  # the self-referential baseline
    )
    contract = yaml.safe_load(out.read_text(encoding="utf-8"))

    assert "profile" not in contract
    assert "fitness_functions" not in contract
    assert contract["fail_threshold"] == 0.75
    assert contract["warn_threshold"] == 0.50
    for module in contract["modules"]:
        assert module["coupling_budget"] == max(
            3, math.ceil(module["fan_out_at_init"] * 1.5)
        ), "the baseline policy must stay self-referential"


@pytest.mark.integration
def test_self_referential_baseline_would_have_passed_this_repo(tangled_repo):
    """Pins *why* the dashboard needs its own policy.

    Regenerating the same repo's contract the way ``archguard init`` does must
    still produce a budget above the measured fan-out -- that behaviour is
    intentional and unchanged, and is exactly what makes it unusable for
    one-off grading.
    """
    import math

    contract = yaml.safe_load((tangled_repo / ".archguard.yml").read_text(encoding="utf-8"))
    svc = next(
        (m for m in contract["modules"] if m["fan_out_at_init"] > 10), None
    )
    assert svc is not None, "fixture no longer has a high-fan-out module"

    self_referential = max(3, math.ceil(svc["fan_out_at_init"] * 1.5))
    assert self_referential > svc["fan_out_at_init"]
    assert svc["coupling_budget"] < self_referential, (
        "the dashboard contract's budget is no better than the self-referential one"
    )


# ---------------------------------------------------------------------------
# Per-layer honesty: a layer with nothing to check must not read as a layer
# that checked and found nothing, and must not be averaged into the score.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_layer1_reports_not_applicable_without_import_rules(tangled_result):
    """L1 enforces only declared import rules; auto-generated contracts have none."""
    layer1 = next(lr for lr in tangled_result.layer_results if lr.layer == 1)

    assert layer1.skipped is True, (
        "Layer 1 scored 0.00 with no rules to enforce and reported itself as a "
        "clean boundary check"
    )
    assert "no import rules" in layer1.skip_reason.lower()


@pytest.mark.integration
def test_skipped_layers_are_excluded_from_the_composite(tangled_result):
    """The score must average only the layers that produced a signal."""
    ran = [lr for lr in tangled_result.layer_results if not lr.skipped]
    assert ran, "expected at least one layer to run"

    expected = sum(lr.score for lr in ran) / len(ran)
    assert tangled_result.composite_score == pytest.approx(expected, abs=1e-6), (
        "composite is diluted by layers that never ran"
    )


def test_extract_layer_results_reads_per_layer_skip_reasons():
    """Per-layer reasons come from metrics, not the run-level skip_reason."""
    from archguard.analysis.scoring import LayerScores
    from archguard.dashboard.pipeline_adapter import _extract_layer_results

    class _Result:
        layer_scores = LayerScores(0.0, 0.4, 0.0, 0.0)
        violations: list = []
        skipped_layers_names = ["Layer 1", "Layer 3"]
        skip_reason = "run-level reason that belongs to no single layer"
        metrics = {
            "layer1_skipped": True,
            "layer1_skip_reason": "no import rules declared",
            "layer3_skipped": True,
            "layer3_skip_reason": "no prior baseline",
        }

    by_layer = {lr.layer: lr for lr in _extract_layer_results(_Result())}

    assert by_layer[1].skipped and by_layer[1].skip_reason == "no import rules declared"
    assert by_layer[3].skipped and by_layer[3].skip_reason == "no prior baseline"
    assert by_layer[2].skipped is False and by_layer[2].skip_reason == ""
    assert by_layer[4].skipped is False
    for lr in by_layer.values():
        assert lr.skip_reason != _Result.skip_reason
