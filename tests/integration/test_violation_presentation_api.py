"""End-to-end checks for how violations are presented and selected via the API.

Covers the three things a user actually sees: every violation stays listed, each
one carries a plain-language explanation, and the counts describe the set the
LLM would really receive (with suppressed findings excluded from it).
"""

from __future__ import annotations

import asyncio

import pytest

from archguard.analysis import violation_kinds
from archguard.dashboard.routes import runs as runs_route
from tests.db_fixtures import requires_postgres

pytestmark = requires_postgres


def _latest(job_id: str, user) -> dict:
    return asyncio.run(runs_route.get_latest_run(job_id=job_id, user=user))


def _violation(module, fan_out, budget=10, layer=2, severity="high"):
    return {
        "module": module,
        "layer": layer,
        "severity": severity,
        "message": f"fan_out={fan_out} exceeds budget={budget}",
        "file": "",
        "line": 0,
        "scope": "module",
        "kind": violation_kinds.FAN_OUT,
        "metrics": {"fan_out": float(fan_out), "budget": float(budget)},
    }


@pytest.fixture
def persisted_run(seed_run):
    """A stored run with more violations than the remediation cap allows."""
    return seed_run(
        score=44.5,
        band="FAIL",
        violations=[_violation(f"module_{i:02d}", fan_out=11 + i) for i in range(20)],
        metrics={
            "fitness_results": [
                {
                    "name": "no_circular_deps",
                    "rule": "graph.cycles == 0",
                    "passed": False,
                    "severity": "critical",
                    "evidence": "Cycle found: lib -> extra -> lib",
                }
            ]
        },
    )


def test_every_violation_is_returned_not_just_the_selected_ones(persisted_run, test_user):
    """The cap limits AI suggestions, never what the table can show."""
    run = _latest(persisted_run, test_user)

    assert len(run["violations"]) == 20
    assert run["remediation_selection"]["selected"] < 20


def test_each_violation_carries_a_plain_language_explanation(persisted_run, test_user):
    run = _latest(persisted_run, test_user)

    for v in run["violations"]:
        plain = v["plain"]
        assert plain["title"]
        assert plain["body"]
        assert plain["technical_details"].startswith("fan_out = ")


def test_selection_counts_describe_what_the_llm_would_receive(persisted_run, test_user):
    run = _latest(persisted_run, test_user)
    sel = run["remediation_selection"]

    assert sel["detected"] == 20
    assert sel["suppressed"] == 0
    assert sel["eligible"] == 20
    assert sel["selected"] == sel["limit"]
    # The failed cycle gate occupies one of the slots sent to the LLM but is not
    # a table row, so the count the UI quotes is one lower.
    assert sel["selected_violations"] == sel["selected"] - 1
    assert len(sel["selected_keys"]) == sel["selected_violations"]


def test_selection_is_stable_across_repeated_reads(persisted_run, test_user):
    first = _latest(persisted_run, test_user)["remediation_selection"]
    second = _latest(persisted_run, test_user)["remediation_selection"]

    assert first["selected_keys"] == second["selected_keys"]


def test_suppressed_violations_are_excluded_from_counts_and_selection(
    persisted_run, test_user
):
    """Suppression hides a finding from the LLM, not from the user.

    Seeded in PostgreSQL against this account rather than through a stubbed
    store, so the path under test is the one production runs: the route
    resolves the caller's suppressions and hands them to the ranking. A stub
    would have agreed that everything worked while the routes were reading a
    file every account shared.
    """
    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.suppression.models import make_violation_hash

    suppressed = {"module_00", "module_01", "module_02"}

    async def _seed() -> None:
        async with session_scope() as session:
            for i, module in enumerate(sorted(suppressed)):
                await store.add_suppression(
                    session,
                    user_id=test_user.id,
                    repo_url="https://github.com/example/repo",
                    module=module,
                    layer=2,
                    violation_hash=make_violation_hash(
                        module, 2, f"fan_out={11 + i} exceeds budget=10"
                    ),
                    reason="accepted debt",
                )

    asyncio.run(_seed())

    run = _latest(persisted_run, test_user)
    sel = run["remediation_selection"]

    assert sel["detected"] == 20, "suppressed findings still count as detected"
    assert sel["suppressed"] == 3
    assert sel["eligible"] == 17
    # Still listed in the table -- suppression hides them from the LLM, not the user.
    assert len(run["violations"]) == 20
    for key in sel["selected_keys"]:
        assert key.split("|")[0] not in suppressed


def test_another_accounts_suppression_does_not_shrink_this_selection(
    persisted_run, test_user, live_db
):
    """The tenancy consequence, at the layer where it costs money.

    A shared store meant a stranger's suppression removed findings from this
    account's remediation plan -- so the LLM was asked to fix a different set of
    problems than the one the user was looking at.
    """
    from archguard.db import store
    from archguard.db.session import session_scope
    from archguard.suppression.models import make_violation_hash

    async def _seed_other_account() -> None:
        async with session_scope() as session:
            stranger = await store.upsert_user(
                session, github_id=9502, login="stranger"
            )
            await store.add_suppression(
                session,
                user_id=stranger.id,
                repo_url="https://github.com/example/repo",
                module="module_00",
                layer=2,
                violation_hash=make_violation_hash(
                    "module_00", 2, "fan_out=11 exceeds budget=10"
                ),
                reason="not this user's decision",
            )

    asyncio.run(_seed_other_account())

    sel = _latest(persisted_run, test_user)["remediation_selection"]
    assert sel["suppressed"] == 0, (
        "another account's suppression removed a finding from this user's plan"
    )
    assert sel["eligible"] == 20
