"""What counts as "the contract changed".

The fingerprint decides whether a scan may reuse anything at all. Get it wrong
in one direction and a repository is re-analysed from scratch every time; get
it wrong in the other and findings measured under one set of rules are carried
forward under another, which reports a repository as clean when it is not.

It was wrong in the first direction, and completely. Every generated contract
records when it was generated, the fingerprint hashed the whole document, so a
repository without a committed `.archguard.yml` produced a different
fingerprint on every scan and `plan_analysis` always answered "the contract
changed". Incremental analysis never engaged for the majority case -- and
nothing noticed, because the answer it gave was always the safe one.

`model_weights_version` belongs to the same class and is easier to miss: it
reads as a model identifier but returns `f"{year}-Q{quarter}"` from the wall
clock and is consulted nowhere in the analysis. Including it would have moved
the same defect onto a quarterly cycle -- every repository re-analysed in full
on the first of January, for nothing.

The direction of caution here is the opposite of the one for carrying findings
forward. An unclassified field is *included*: over-invalidating costs a slow
scan, under-invalidating costs a wrong answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from archguard.cache import incremental

#: Something set in every field that decides what counts as a violation, so
#: each one can be shown to matter.
SEMANTIC = {
    "version": "3.0",
    "modules": [
        {
            "name": "pkg",
            "path": "pkg/",
            "coupling_budget": 3,
            "semantic_drift_threshold": 0.25,
            "duplication_threshold": 0.5,
            "allowed_imports": ["other"],
            "disallowed_imports": ["forbidden"],
            "module_names": ["pkg"],
            "fan_out_at_init": 2,
        }
    ],
    "skip_layers": ["semantic"],
    "fail_threshold": 0.75,
    "warn_threshold": 0.50,
    "weights": {"layer1": 0.25, "layer2": 0.25, "layer3": 0.25, "layer4": 0.25},
    "fitness_functions": [
        {"name": "no_cycles", "rule": "graph.cycles == 0", "severity": "critical"}
    ],
    "profile": "ci",
}


def fp(contract: dict) -> str:
    return incremental.contract_fingerprint(contract)


# --------------------------------------------------- provenance is not meaning


def test_when_a_contract_was_generated_does_not_change_what_it_means():
    """The reported defect, at its source."""
    first = {
        **SEMANTIC,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "generated_by": "archguard init",
        "model_weights_version": "2026-Q1",
    }
    second = {
        **SEMANTIC,
        "generated_at": "2026-08-30T15:04:05+00:00",
        "generated_by": "archguard init (directory heuristic fallback: sparse_history)",
        "model_weights_version": "2026-Q3",
    }
    assert fp(first) == fp(second), (
        "when and how the contract was written changed what it was taken to mean"
    )


def test_a_handwritten_contract_matches_the_generated_one_it_equals():
    """A committed `.archguard.yml` carries none of the provenance keys.

    If it fingerprinted differently, committing the file ArchGuard generated
    would itself look like a change to the rules.
    """
    assert fp(SEMANTIC) == fp({**SEMANTIC, "generated_at": "2026-01-01T00:00:00+00:00"})


# ------------------------------------------------- but every rule still counts


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("version", "4.0"),
        ("skip_layers", []),
        ("fail_threshold", 0.9),
        ("warn_threshold", 0.1),
        ("weights", {"layer1": 1.0, "layer2": 0.0, "layer3": 0.0, "layer4": 0.0}),
        ("profile", "strict"),
        (
            "fitness_functions",
            [{"name": "no_cycles", "rule": "graph.cycles < 5", "severity": "high"}],
        ),
    ],
)
def test_every_top_level_rule_invalidates_the_cache(field, changed):
    """Pinned one field at a time rather than by a single example.

    Each of these decides an outcome -- which layers run, where the band falls,
    how the composite is weighted, which gate fails. A field quietly dropped
    from the fingerprint means findings measured under the old value get
    carried forward under the new one.
    """
    assert fp(SEMANTIC) != fp({**SEMANTIC, field: changed}), (
        f"changing {field} did not invalidate the cache"
    )


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("name", "renamed"),
        ("path", "elsewhere/"),
        ("coupling_budget", 99),
        ("semantic_drift_threshold", 0.9),
        ("duplication_threshold", 0.95),
        ("allowed_imports", ["something-else"]),
        ("disallowed_imports", []),
        ("module_names", ["different"]),
        ("fan_out_at_init", 40),
    ],
)
def test_every_module_rule_invalidates_the_cache(field, changed):
    """Most thresholds live per module, so the same again one level down."""
    altered = {**SEMANTIC, "modules": [{**SEMANTIC["modules"][0], field: changed}]}
    assert fp(SEMANTIC) != fp(altered), (
        f"changing modules[0].{field} did not invalidate the cache"
    )


def test_adding_or_removing_a_module_invalidates_the_cache():
    assert fp(SEMANTIC) != fp(
        {**SEMANTIC, "modules": [*SEMANTIC["modules"], {"name": "extra", "path": "x/"}]}
    )
    assert fp(SEMANTIC) != fp({**SEMANTIC, "modules": []})


def test_an_unclassified_field_invalidates_the_cache():
    """The safe default, and the reason this is a blacklist.

    A contract setting nobody has thought about invalidates until somebody
    decides it should not, so the failure mode of forgetting is a slow scan
    rather than a wrong one.
    """
    assert fp(SEMANTIC) != fp({**SEMANTIC, "some_future_setting": True})


def test_reserialisation_and_key_order_do_not_matter():
    assert fp(SEMANTIC) == fp(dict(reversed(list(SEMANTIC.items()))))


def test_the_ephemeral_set_holds_only_provenance():
    """A guard on the list itself.

    Adding a key here is how a real rule stops invalidating the cache, so the
    contents are pinned rather than left to review. Each is metadata about how
    the file came to exist, and none is read by the analysis.
    """
    assert frozenset(
        {"generated_at", "generated_by", "model_weights_version"}
    ) == incremental.EPHEMERAL_CONTRACT_FIELDS


# ------------------------------------------- and the plan behaves accordingly


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("import os\n", encoding="utf-8")
    return root


PLAN_CONTRACT = {"version": "3.0", "modules": [{"name": "pkg", "path": "pkg/"}]}


def _previous(contract: dict, hashes: dict, version: str = "0.3.0"):
    return incremental.PreviousRun(
        contract=contract,
        archguard_version=version,
        file_hashes=hashes,
        violations=[],
    )


def test_a_regenerated_contract_no_longer_forces_a_full_analysis(repo):
    """The consequence, through the thing that consumes the fingerprint.

    Two scans of an untouched repository, the contract generated afresh each
    time -- which is what happens for any repository without a committed
    `.archguard.yml`.
    """
    files = sorted(repo.rglob("*.py"))
    known = incremental.hash_files(files, repo)

    plan = incremental.plan_analysis(
        files=files,
        root=repo,
        contract={**PLAN_CONTRACT, "generated_at": "2026-08-30T15:04:05+00:00"},
        version="0.3.0",
        previous=_previous(
            {**PLAN_CONTRACT, "generated_at": "2026-01-01T00:00:00+00:00"}, known
        ),
    )

    assert plan.full is False, f"still a full analysis: {plan.reason}"
    assert plan.changed == []


def test_a_real_contract_change_still_forces_a_full_analysis(repo):
    """Regenerating has to stay distinguishable from editing."""
    files = sorted(repo.rglob("*.py"))
    known = incremental.hash_files(files, repo)

    plan = incremental.plan_analysis(
        files=files,
        root=repo,
        contract={
            "version": "3.0",
            "modules": [{"name": "pkg", "path": "pkg/", "coupling_budget": 99}],
            "generated_at": "2026-08-30T15:04:05+00:00",
        },
        version="0.3.0",
        previous=_previous(
            {**PLAN_CONTRACT, "generated_at": "2026-01-01T00:00:00+00:00"}, known
        ),
    )

    assert plan.full is True
    assert "contract" in plan.reason.lower()


def test_a_version_bump_still_forces_a_full_analysis(repo):
    """The other invalidation, unaffected by any of this: a newer analyser may
    detect what the old one could not, and carrying its findings forward would
    hide exactly the new detections."""
    files = sorted(repo.rglob("*.py"))
    known = incremental.hash_files(files, repo)
    contract = {**PLAN_CONTRACT, "generated_at": "2026-01-01T00:00:00+00:00"}

    plan = incremental.plan_analysis(
        files=files, root=repo, contract=contract, version="0.4.0",
        previous=_previous(contract, known, version="0.3.0"),
    )

    assert plan.full is True
    assert "version" in plan.reason.lower()
