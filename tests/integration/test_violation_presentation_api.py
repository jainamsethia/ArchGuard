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
    """Suppressions come from PostgreSQL, owned by the user asking.

    Real rows rather than a patched seam: the point of moving off the JSONL
    store was that the route, the store and the ranking agree on one source, and
    a stubbed store would not have noticed if they stopped agreeing.
    """
    from archguard.db.session import session_scope
    from archguard.db.store import add_suppression
    from archguard.suppression.models import make_violation_hash

    suppressed = {"module_00", "module_01", "module_02"}

    async def _seed() -> None:
        async with session_scope() as session:
            for i, module in enumerate(sorted(suppressed)):
                message = f"fan_out={11 + i} exceeds budget=10"
                await add_suppression(
                    session,
                    repo_url="https://github.com/example/repo",
                    module=module,
                    layer=2,
                    violation_hash=make_violation_hash(module, 2, message),
                    reason="accepted debt",
                    user_id=test_user.id,
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


def test_one_users_suppressions_do_not_affect_another(persisted_run, test_user, seed_run):
    """The reason this moved to PostgreSQL at all.

    The JSONL store was keyed by repository, so everybody analysing the same
    repository shared one file -- and one another's reasons. Rows are owned.
    """
    from archguard.db.models import User
    from archguard.db.session import session_scope
    from archguard.db.store import active_violation_hashes, add_suppression
    from archguard.suppression.models import make_violation_hash

    message = "fan_out=11 exceeds budget=10"

    async def _seed_other_user() -> int:
        async with session_scope() as session:
            other = User(github_id=987654, login="somebody-else")
            session.add(other)
            await session.flush()
            await add_suppression(
                session,
                repo_url="https://github.com/example/repo",
                module="module_00",
                layer=2,
                violation_hash=make_violation_hash("module_00", 2, message),
                reason="not your business",
                user_id=other.id,
            )
            return int(other.id)

    other_id = asyncio.run(_seed_other_user())

    async def _hashes(user_id: int) -> set:
        async with session_scope() as session:
            return await active_violation_hashes(
                session, "https://github.com/example/repo", user_id
            )

    assert asyncio.run(_hashes(other_id)), "the other user has their own suppression"
    assert asyncio.run(_hashes(test_user.id)) == set(), (
        "somebody else's suppression must not apply to this user"
    )

    # And it does not silently hide their findings either.
    run = _latest(persisted_run, test_user)
    assert run["remediation_selection"]["suppressed"] == 0
