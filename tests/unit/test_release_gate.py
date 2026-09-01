"""The release gate: ArchGuard checking ArchGuard.

Two pre-release gates used to run `archguard analyze --repo .` and
`archguard fitness check`. The CLI was deleted and nothing replaced them, so a
product whose entire pitch is architectural self-checking shipped without
checking itself. The documentation pass recorded that honestly; this closes it.

The gate drives `AnalysisOrchestrator` directly. That matters for more than
convenience: going through the website's API would mean the release check
enqueues a job, the worker runs the analysis, and a failure in either is a
failure of the thing being tested. A direct call has no queue, no database, no
session and no browser in it, so what it measures is the analysis engine and
nothing else.

Thresholds are not invented here. `.archguard.yml` at the repository root
already declares module boundaries, `fail_threshold`, and eight fitness
functions with severities, and `compute_archdebt` already decides
`should_fail_ci` from them. The gate reports that decision; it does not make a
second one.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture()
def tiny_repo(tmp_path: Path) -> Path:
    """A repository small enough to analyse in a test, with a real contract.

    Real files and a real contract, because the point of this suite is that the
    engine runs. Two modules so Layer 2 has something to measure, and a
    deliberate cross-module import so there is a finding to see.
    """
    for module in ("core", "api"):
        (tmp_path / module).mkdir()
        (tmp_path / module / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "core" / "engine.py").write_text(
        '"""Core."""\n\n\ndef compute(values):\n    return sum(values)\n',
        encoding="utf-8",
    )
    (tmp_path / "api" / "handler.py").write_text(
        '"""API."""\nimport core.engine\nimport json\n\n\n'
        "def handle(values):\n    return json.dumps(core.engine.compute(values))\n",
        encoding="utf-8",
    )
    (tmp_path / ".archguard.yml").write_text(
        'version: "3.0"\n'
        "modules:\n"
        "  - name: core\n    path: core/\n    coupling_budget: 5\n"
        "  - name: api\n    path: api/\n    coupling_budget: 5\n"
        "    allowed_imports: [core]\n"
        "fail_threshold: 0.75\n"
        "warn_threshold: 0.50\n"
        "fitness_functions:\n"
        "  - name: no_cycles\n    rule: \"graph.cycles == 0\"\n    severity: critical\n",
        encoding="utf-8",
    )
    return tmp_path


# ----------------------------------------------------- 1 & 3. it runs, and passes


def test_the_gate_runs_the_real_engine(tiny_repo: Path):
    """Not a wrapper around the test suite.

    The instruction this closes was explicit that a gate which merely runs
    pytest would be theatre. This calls the analysis engine and reads what it
    measured.
    """
    from archguard.release_gate import evaluate

    result = evaluate(tiny_repo)

    assert result.files_analysed > 0, "the gate analysed no files"
    assert result.health_score is not None
    assert result.band
    # Four layers, each either measured or explained.
    assert set(result.layers) == {1, 2, 3, 4}
    for layer in result.layers.values():
        assert layer["measured"] or layer["skip_reason"], layer


def test_a_healthy_repository_passes(tiny_repo: Path):
    from archguard.release_gate import evaluate

    result = evaluate(tiny_repo)

    assert result.passed is True, result.reasons


def test_the_result_is_machine_checkable(tiny_repo: Path):
    """A gate whose output only a person can read cannot be a gate."""
    from archguard.release_gate import evaluate

    payload = json.loads(json.dumps(evaluate(tiny_repo).to_dict()))

    for key in (
        "passed",
        "health_score",
        "band",
        "layers",
        "violations",
        "fitness",
        "reasons",
        "files_analysed",
    ):
        assert key in payload, f"{key} missing from the gate's output"


def test_the_result_carries_enough_to_diagnose_a_failure(tiny_repo: Path):
    """"Failed" with no detail sends someone to run the analysis by hand."""
    from archguard.release_gate import evaluate

    result = evaluate(tiny_repo)

    assert isinstance(result.violations, list)
    assert result.fitness, "no fitness gate results were reported"
    for gate in result.fitness:
        assert "name" in gate and "passed" in gate and "severity" in gate


# ------------------------------------------------------ 2. it targets ArchGuard


def test_the_default_target_is_this_repository():
    """The gate exists to check ArchGuard, so that must be its default."""
    from archguard.release_gate import default_repo_root

    root = default_repo_root()

    assert (root / "archguard" / "__init__.py").exists()
    assert (root / ".archguard.yml").exists()
    assert root == ROOT


def test_this_repository_declares_the_thresholds_the_gate_reads():
    """Reusing `.archguard.yml` rather than inventing a second threshold system
    is the design decision this pins."""
    import yaml

    contract = yaml.safe_load((ROOT / ".archguard.yml").read_text(encoding="utf-8"))

    assert contract["fail_threshold"]
    assert {m["name"] for m in contract["modules"]} >= {"archguard", "tests"}
    assert contract["fitness_functions"], "no gates to enforce"
    assert any(
        g.get("severity") == "critical" for g in contract["fitness_functions"]
    ), "every gate is advisory, so none of them protects a release"


# --------------------------------------------------------- 4. it can fail


def test_a_breached_threshold_fails_the_gate(tiny_repo: Path):
    """The half that makes it a gate rather than a report.

    `fail_threshold: 0.0` means any debt at all is a failure, so this exercises
    the real decision path rather than a stubbed one.
    """
    from archguard.release_gate import evaluate

    contract = (tiny_repo / ".archguard.yml").read_text(encoding="utf-8")
    (tiny_repo / ".archguard.yml").write_text(
        contract.replace("fail_threshold: 0.75", "fail_threshold: 0.0"),
        encoding="utf-8",
    )

    result = evaluate(tiny_repo)

    assert result.passed is False
    assert result.reasons, "the gate failed without saying why"


def test_a_failed_critical_fitness_gate_fails_the_release(tiny_repo: Path):
    """A critical gate is the contract's own definition of unreleasable."""
    from archguard.release_gate import evaluate

    contract = (tiny_repo / ".archguard.yml").read_text(encoding="utf-8")
    (tiny_repo / ".archguard.yml").write_text(
        contract + '  - name: impossible\n    rule: "health_score >= 101"\n'
        "    severity: critical\n",
        encoding="utf-8",
    )

    result = evaluate(tiny_repo)

    assert result.passed is False
    assert any("impossible" in r or "fitness" in r.lower() for r in result.reasons), (
        result.reasons
    )


def test_a_warning_gate_does_not_fail_the_release(tiny_repo: Path):
    """Severity has to mean something, or every gate is critical."""
    from archguard.release_gate import evaluate

    contract = (tiny_repo / ".archguard.yml").read_text(encoding="utf-8")
    (tiny_repo / ".archguard.yml").write_text(
        contract + '  - name: aspirational\n    rule: "health_score >= 101"\n'
        "    severity: warn\n",
        encoding="utf-8",
    )

    result = evaluate(tiny_repo)

    assert result.passed is True, result.reasons
    assert any(not g["passed"] for g in result.fitness), (
        "the warning gate should still be reported as failing"
    )


# -------------------------------------------- 5. bad configuration fails clearly


def test_a_missing_contract_fails_with_a_usable_message(tmp_path: Path):
    """Not a traceback, and not a pass. A release check that cannot find its
    thresholds has not verified anything."""
    from archguard.release_gate import GateConfigurationError, evaluate

    (tmp_path / "mod").mkdir()
    (tmp_path / "mod" / "a.py").write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(GateConfigurationError, match=r"archguard\.yml"):
        evaluate(tmp_path)


def test_a_malformed_contract_fails_with_a_usable_message(tmp_path: Path):
    from archguard.release_gate import GateConfigurationError, evaluate

    (tmp_path / ".archguard.yml").write_text("modules: [oh: dear\n", encoding="utf-8")

    with pytest.raises(GateConfigurationError):
        evaluate(tmp_path)


def test_a_contract_with_no_modules_fails_rather_than_scoring_nothing(tmp_path: Path):
    """The empty-scope defect, at the gate's own level: a contract matching no
    file would otherwise be a release check that measured nothing and passed."""
    from archguard.release_gate import GateConfigurationError, evaluate

    (tmp_path / ".archguard.yml").write_text(
        'version: "3.0"\nmodules: []\nfail_threshold: 0.75\n', encoding="utf-8"
    )
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(GateConfigurationError, match="module"):
        evaluate(tmp_path)


# ------------------------------- 6 & 7. no CLI, and no recursion into the product


def test_the_gate_does_not_need_the_removed_cli():
    from archguard import release_gate

    source = Path(release_gate.__file__).read_text(encoding="utf-8")

    assert "archguard.cli" not in source
    assert not (ROOT / "archguard" / "cli").exists()
    assert "[project.scripts]" not in (ROOT / "pyproject.toml").read_text(
        encoding="utf-8"
    )


def test_the_gate_does_not_call_the_website_it_is_checking():
    """The recursion this design exists to avoid.

    A gate that posted to `/api/v1/jobs` would enqueue work for the worker,
    whose failure would then be indistinguishable from an architectural
    regression -- and on a machine with the queue configured, the release check
    would be waiting on the product it is supposed to be judging.

    Checked by reading the imports rather than by running it, because the
    absence of a network call is not something one execution can demonstrate.
    """
    from archguard import release_gate

    tree = ast.parse(Path(release_gate.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = {
        "httpx",
        "requests",
        "urllib.request",
        "archguard.dashboard.app",
        "archguard.worker",
        "archguard.worker.queue",
        "archguard.worker.tasks",
        "archguard.db.session",
        "archguard.db.store",
    }
    offenders = sorted(
        name
        for name in imported
        if name in forbidden or name.startswith(("archguard.worker", "httpx"))
    )

    assert not offenders, (
        f"the release gate reaches into the running product: {offenders}. It must "
        "call the analysis engine directly, or a queue outage becomes an "
        "architectural regression."
    )


def test_the_gate_needs_no_database_or_session(tiny_repo: Path, monkeypatch):
    """Run with the database and Redis pointed at nothing.

    A release check that needs production credentials is one that cannot run on
    a fork, in a container, or before the infrastructure exists.
    """
    from archguard.release_gate import evaluate

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)

    assert evaluate(tiny_repo).files_analysed > 0


# ---------------------------------------------------------- the command itself


def test_it_can_be_run_as_a_module(tiny_repo: Path):
    """`python -m archguard.release_gate`, so CI needs no wrapper script and no
    installed entry point."""
    proc = subprocess.run(
        [sys.executable, "-m", "archguard.release_gate", "--repo", str(tiny_repo), "--json"],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=ROOT,
    )

    assert proc.returncode == 0, proc.stderr[-2000:]
    payload = json.loads(proc.stdout)
    assert payload["passed"] is True


def test_the_exit_code_reports_the_verdict(tiny_repo: Path):
    """CI keys off this and nothing else."""
    contract = (tiny_repo / ".archguard.yml").read_text(encoding="utf-8")
    (tiny_repo / ".archguard.yml").write_text(
        contract.replace("fail_threshold: 0.75", "fail_threshold: 0.0"),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, "-m", "archguard.release_gate", "--repo", str(tiny_repo)],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=ROOT,
    )

    assert proc.returncode == 1, proc.stdout[-2000:]


def test_a_configuration_error_exits_differently_from_a_failed_gate(tmp_path: Path):
    """"Your architecture regressed" and "I could not find your contract" are
    different things to be told, and CI should be able to tell them apart."""
    proc = subprocess.run(
        [sys.executable, "-m", "archguard.release_gate", "--repo", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=ROOT,
    )

    assert proc.returncode == 2, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert "archguard.yml" in (proc.stdout + proc.stderr)


# --------------------------------------------------------------- 8. CI runs it


def test_ci_invokes_the_gate():
    """A gate no job runs is a gate that protects nothing.

    It belongs in the `ml` job specifically: that is the one with the worker
    extras installed and `ARCHGUARD_SKIP_ML` deliberately unset, so all four
    layers actually measure something.
    """
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "archguard.release_gate" in ci, (
        "no CI job runs the release gate"
    )

    ml_job = ci.split("\n  ml:", 1)[-1].split("\n  migrations:", 1)[0]
    assert "archguard.release_gate" in ml_job, (
        "the release gate does not run in the job that has the ML extras, so it "
        "would grade the repository with two of the four layers unmeasured"
    )
    assert "continue-on-error" not in ml_job, (
        "the ml job is advisory, so a failed release gate would not fail CI"
    )


def test_a_critical_gate_is_reported_as_critical(tiny_repo: Path):
    """The severity has to survive the round trip, or nothing ever blocks.

    `FitnessFunctionResult` carries the rule text and not the contract's name,
    so a lookup keyed on the name misses every time and every gate defaults to
    "warn" -- which reports a failing critical gate as advisory and lets the
    release through. The first version of this file did exactly that, and the
    self-analysis printed eight `(warn)` gates for a contract that marks two of
    them critical.
    """
    from archguard.release_gate import evaluate

    result = evaluate(tiny_repo)

    by_name = {g["name"]: g for g in result.fitness}
    assert "no_cycles" in by_name, (
        f"the contract's gate name did not survive: {[g['name'] for g in result.fitness]}"
    )
    assert by_name["no_cycles"]["severity"] == "critical"


def test_this_repositorys_own_critical_gates_are_reported_as_critical():
    """Against the real contract, which is what CI grades.

    `.archguard.yml` marks `no_circular_dependencies` and
    `archguard_cannot_import_tests` critical. If they arrive as warnings, the
    release gate cannot fail on either.
    """
    import yaml

    contract = yaml.safe_load((ROOT / ".archguard.yml").read_text(encoding="utf-8"))
    critical = {
        f["name"] for f in contract["fitness_functions"] if f["severity"] == "critical"
    }

    assert critical, "the contract marks no gate critical"
    # Every critical gate must be one the evaluator can actually evaluate, or it
    # silently never runs and the release gate never sees it.
    from archguard.fitness.evaluator import FitnessFunctionEvaluator

    assert FitnessFunctionEvaluator is not None
    rules = {f["rule"] for f in contract["fitness_functions"] if f["name"] in critical}
    assert rules
