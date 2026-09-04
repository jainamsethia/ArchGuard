"""ArchGuard checking ArchGuard, before a release.

Two pre-release gates used to run `archguard analyze --repo .` and `archguard
fitness check`. The CLI was deleted and nothing replaced them, so a product
whose whole subject is architectural self-checking shipped without checking
itself.

    python -m archguard.release_gate            # this repository, human output
    python -m archguard.release_gate --json     # the same, machine-readable

Exit codes, because CI keys off them: 0 released, 1 the architecture failed its
own contract, 2 the check could not be performed. The third is separate on
purpose -- "your architecture regressed" and "I could not find your contract"
are different things to be told, and a gate that reported a missing file as a
passing build would be worse than no gate.

It calls `AnalysisOrchestrator` directly. Not the HTTP API, which would enqueue
a job for the worker and make a queue outage indistinguishable from an
architectural regression -- and, on a machine with the queue configured, would
leave the release check waiting on the product it is meant to be judging. There
is no database, no session, no browser and no network in this path, so it runs
on a fork, in a container, and before any infrastructure exists.

Thresholds live in `.archguard.yml`, which already declares the module
boundaries, `fail_threshold` and the fitness functions with their severities.
`compute_archdebt` already decides `should_fail_ci` from them. This reports that
decision rather than making a second one, because two threshold systems
disagree eventually and the argument is never resolved in the failing build.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Directories that are never part of the repository being judged. Virtual
#: environments in particular: they hold thousands of third-party files, and
#: including them would measure pip's output rather than ours.
_EXCLUDED_DIRS = frozenset(
    {".venv", "venv", "node_modules", "test_venv_all", "test_venv_ml", "build", "dist"}
)

#: Test *data*, not source. `tests/fixtures/` holds deliberately planted
#: violations that the suite asserts the analyser finds: a module importing a,
#: b, c and d to breach a coupling budget, a forbidden `db` import, a file whose
#: name is a fake credential, and one that does not parse at all.
#:
#: Feeding them to the gate measured the test data as this repository's
#: architecture -- seven of the twenty-one imports attributed to `tests` came
#: from there, which is most of the distance to its coupling budget. Excluded
#: rather than the budget raised: the budget was not wrong, the input was.
#: `pyproject.toml` force-excludes the same directory from ruff, for the same
#: reason.
_EXCLUDED_PREFIXES = ("tests/fixtures/",)


class GateConfigurationError(RuntimeError):
    """The check could not be performed. Distinct from the check failing."""


@dataclass
class GateResult:
    """What the gate measured, and whether it lets the release through."""

    passed: bool
    health_score: float
    band: str
    composite_score: float
    files_analysed: int
    #: Per layer: whether it measured anything, its score, and why not.
    layers: dict[int, dict[str, Any]] = field(default_factory=dict)
    violations: list[dict[str, Any]] = field(default_factory=list)
    fitness: list[dict[str, Any]] = field(default_factory=list)
    #: Why it failed, in the order a reader would want them. Empty when passed.
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "health_score": self.health_score,
            "band": self.band,
            "composite_score": self.composite_score,
            "files_analysed": self.files_analysed,
            "layers": {str(k): v for k, v in sorted(self.layers.items())},
            "violations": self.violations,
            "fitness": self.fitness,
            "reasons": self.reasons,
        }


def default_repo_root() -> Path:
    """This repository. The gate exists to check it."""
    return Path(__file__).resolve().parents[1]


def _python_files(root: Path) -> list[Path]:
    from archguard.utils.paths import is_vendored

    found = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if _EXCLUDED_DIRS.intersection(relative.parts):
            continue
        if any(part.startswith(".") for part in relative.parts):
            continue
        if relative.as_posix().startswith(_EXCLUDED_PREFIXES):
            continue
        if is_vendored(path, root):
            continue
        found.append(path)
    return sorted(found)


def _load_contract(root: Path) -> dict[str, Any]:
    contract_path = root / ".archguard.yml"
    if not contract_path.exists():
        raise GateConfigurationError(
            f"No .archguard.yml at {root}. The release gate reads its thresholds "
            "from the contract; without one there is nothing to check against."
        )
    try:
        from archguard.contract.loader import load_contract

        contract = load_contract(root)
    except Exception as exc:
        raise GateConfigurationError(
            f"Could not read {contract_path}: {exc}"
        ) from exc

    modules = contract.get("modules") or []
    if not modules:
        # The empty-scope defect at the gate's own level: a contract declaring
        # no module measures nothing, and a release check that measured nothing
        # must not report a pass.
        raise GateConfigurationError(
            f"{contract_path} declares no module, so the analysis would measure "
            "nothing and the gate would pass without checking anything."
        )
    return contract


def evaluate(repo_root: Path | None = None) -> GateResult:
    """Analyse *repo_root* and decide whether it may be released.

    Raises `GateConfigurationError` when the check cannot be performed, which
    the caller must not treat as a pass.
    """
    root = Path(repo_root) if repo_root is not None else default_repo_root()
    root = root.resolve()
    contract = _load_contract(root)

    files = _python_files(root)
    if not files:
        raise GateConfigurationError(
            f"No Python files under {root} to analyse."
        )

    from archguard.analysis.layers import AnalysisOrchestrator

    orchestrator = AnalysisOrchestrator(repo_root=root)
    with orchestrator:
        result = orchestrator.run(
            changed_files=files,
            commit_sha=_commit_sha(root),
            quiet=True,
        )

    return _to_gate_result(result, contract, len(files))


def _commit_sha(root: Path) -> str:
    from archguard.analysis.layers import AnalysisOrchestrator

    try:
        return AnalysisOrchestrator.get_commit_sha(root) or "0" * 40
    except Exception:
        # A tarball or a fresh directory has no git history. The sha is only
        # stamped onto findings, so not having one is not a reason to refuse.
        return "0" * 40


def _to_gate_result(result: Any, contract: dict[str, Any], files: int) -> GateResult:
    archdebt = result.archdebt
    skipped = set(getattr(result, "skipped_layers_names", []) or [])
    metrics = getattr(result, "metrics", None) or {}
    scores = {
        1: archdebt.layer_scores.layer1_violation,
        2: archdebt.layer_scores.layer2_coupling,
        3: archdebt.layer_scores.layer3_drift,
        4: archdebt.layer_scores.layer4_duplication,
    }

    layers = {
        n: {
            "measured": f"Layer {n}" not in skipped,
            "score": scores[n],
            "skip_reason": str(metrics.get(f"layer{n}_skip_reason", "") or ""),
        }
        for n in (1, 2, 3, 4)
    }

    violations = [
        {
            "layer": getattr(v, "layer", None),
            "module": getattr(v, "module", None),
            "severity": getattr(getattr(v, "severity", None), "value", None)
            or str(getattr(v, "severity", "")),
            "message": getattr(v, "message", ""),
            "file": getattr(v, "file_path", "") or None,
        }
        for v in result.violations
    ]

    # Keyed on the rule, not the name. `FitnessFunctionResult` carries only the
    # rule text -- the contract's `name` never reaches it -- which is also how
    # `apply_fitness_results` matches configs. Keying on the name instead made
    # every lookup miss and every gate default to "warn", so a failing critical
    # gate would have been reported as advisory and let a release through: the
    # one outcome this whole file exists to prevent.
    by_rule = {
        str(f.get("rule", "")): f for f in (contract.get("fitness_functions") or [])
    }
    fitness = []
    for outcome in archdebt.fitness_results:
        rule = str(getattr(outcome, "rule", ""))
        configured = by_rule.get(rule, {})
        fitness.append(
            {
                "name": str(configured.get("name") or rule or "?"),
                "rule": rule,
                "passed": bool(getattr(outcome, "passed", True)),
                "severity": str(configured.get("severity", "warn")).lower(),
                "evidence": str(getattr(outcome, "details", "") or ""),
            }
        )

    reasons: list[str] = []
    if archdebt.should_fail_ci:
        reasons.extend(archdebt.fail_reasons or ["composite score breached the contract's fail_threshold"])
    for gate in fitness:
        if not gate["passed"] and gate["severity"] == "critical":
            reasons.append(
                f"critical fitness gate failed: {gate['name']}"
                + (f" ({gate['evidence']})" if gate["evidence"] else "")
            )

    return GateResult(
        # A warning gate is reported and does not block: severity has to mean
        # something or every gate is critical and none of them is read.
        passed=not reasons,
        health_score=archdebt.health_score,
        band=str(archdebt.band.name),
        composite_score=archdebt.composite_score,
        files_analysed=files,
        layers=layers,
        violations=violations,
        fitness=fitness,
        reasons=reasons,
    )


def _render(result: GateResult) -> str:
    lines = [
        "ArchGuard release gate",
        "",
        f"  Health      {result.health_score}/100  ({result.band})",
        f"  Composite   {result.composite_score:.3f}",
        f"  Files       {result.files_analysed}",
        "",
        "  Layers",
    ]
    names = {1: "import boundaries", 2: "coupling", 3: "semantic drift", 4: "duplication"}
    for n, layer in sorted(result.layers.items()):
        if layer["measured"]:
            lines.append(f"    L{n} {names[n]:<20} {layer['score']:.3f}")
        else:
            lines.append(f"    L{n} {names[n]:<20} not measured ({layer['skip_reason']})")

    lines += ["", f"  Violations  {len(result.violations)}"]
    for v in result.violations[:10]:
        where = f" {v['file']}" if v["file"] else ""
        lines.append(f"    L{v['layer']} {v['module']}: {v['message']}{where}")
    if len(result.violations) > 10:
        lines.append(f"    ... and {len(result.violations) - 10} more")

    lines += ["", "  Fitness gates"]
    for gate in result.fitness:
        mark = "pass" if gate["passed"] else "FAIL"
        lines.append(f"    [{mark}] {gate['name']} ({gate['severity']})")

    lines += [""]
    if result.passed:
        lines.append("  PASS - the repository meets its own contract.")
    else:
        lines.append("  FAIL - this release is blocked:")
        lines.extend(f"    - {reason}" for reason in result.reasons)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m archguard.release_gate",
        description="Analyse a repository against its own .archguard.yml and "
        "report whether it may be released.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="repository to check (default: the ArchGuard repository itself)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the result as JSON on stdout"
    )
    args = parser.parse_args(argv)

    try:
        result = evaluate(args.repo)
    except GateConfigurationError as exc:
        # Exit 2, never 0. A check that could not run has not passed.
        print(f"release gate could not run: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result.to_dict(), indent=2) if args.json else _render(result))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
