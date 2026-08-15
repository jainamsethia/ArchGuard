"""Unit tests for the RemediationPlan engine (Phase 4 Step 15)."""

import json
from unittest.mock import patch, MagicMock
import httpx
import pytest

from archguard.llm.remediation import (
    RemediationEngine,
    RemediationProvider,
    RemediationTask,
    OpenAIRemediationProvider,
    RemediationUnavailableError,
)


# ---------------------------------------------------------------------------
# Mock provider for deterministic tests
# ---------------------------------------------------------------------------


class MockRemediationProvider(RemediationProvider):
    def __init__(
        self,
        tasks: list[RemediationTask] | None = None,
        raises: Exception | None = None,
    ):
        self._tasks = tasks or []
        self._raises = raises
        self.last_context = ""

    def generate_tasks(self, context: str) -> list[RemediationTask]:
        self.last_context = context
        if self._raises:
            raise self._raises
        return list(self._tasks)


def _task(
    title: str,
    priority: str = "high",
    effort: int = 3,
    criteria: list[str] | None = None,
) -> RemediationTask:
    return RemediationTask(
        title=title,
        description=f"Description for {title}",
        priority=priority,
        effort_days=effort,
        acceptance_criteria=criteria or ["Criterion A"],
    )


# ---------------------------------------------------------------------------
# RemediationEngine — context builder
# ---------------------------------------------------------------------------


def test_engine_empty_findings_returns_empty_plan():
    provider = MockRemediationProvider()
    engine = RemediationEngine(provider)
    plan = engine.plan({})
    assert plan.total == 0
    assert provider.last_context == ""  # no context built → provider not called


def test_engine_violation_findings_builds_context():
    provider = MockRemediationProvider()
    engine = RemediationEngine(provider)
    engine.plan(
        {
            "score": 55.0,
            "band": "WARN",
            "violations": [
                {
                    "layer": 1,
                    "module": "auth",
                    "message": "Illegal import",
                    "severity": "critical",
                },
            ],
        }
    )
    ctx = provider.last_context
    assert "Health Score: 55.00" in ctx
    assert "Illegal import" in ctx
    assert "severity=critical" in ctx


def test_engine_fitness_failure_builds_context():
    provider = MockRemediationProvider()
    engine = RemediationEngine(provider)
    engine.plan(
        {
            "fitness_failures": [
                {
                    "name": "No DB calls in domain layer",
                    "evidence": "Found 3 violations",
                },
            ],
        }
    )
    ctx = provider.last_context
    assert "No DB calls in domain layer" in ctx
    assert "Found 3 violations" in ctx


def test_engine_score_only_builds_context():
    provider = MockRemediationProvider()
    engine = RemediationEngine(provider)
    engine.plan({"score": 40.0, "band": "FAIL"})
    assert "Health Score: 40.00" in provider.last_context


# ---------------------------------------------------------------------------
# RemediationEngine — priority grouping
# ---------------------------------------------------------------------------


def test_engine_groups_tasks_by_priority():
    tasks = [
        _task("Fix critical issue", "critical"),
        _task("Fix high issue", "high"),
        _task("Fix medium issue", "medium"),
        _task("Fix low issue", "low"),
    ]
    engine = RemediationEngine(MockRemediationProvider(tasks))
    plan = engine.plan({"score": 50.0})

    assert len(plan.critical) == 1
    assert len(plan.high) == 1
    assert len(plan.medium) == 1
    assert len(plan.low) == 1
    assert plan.critical[0].title == "Fix critical issue"


def test_engine_invalid_priority_falls_back_to_medium():
    bad_task = RemediationTask("Bad priority task", "desc", "super-urgent", 2, ["AC1"])
    engine = RemediationEngine(MockRemediationProvider([bad_task]))
    plan = engine.plan({"score": 50.0})
    assert len(plan.medium) == 1
    assert plan.medium[0].title == "Bad priority task"


def test_engine_all_tasks_property():
    tasks = [
        _task("A", "critical"),
        _task("B", "high"),
        _task("C", "medium"),
        _task("D", "low"),
    ]
    engine = RemediationEngine(MockRemediationProvider(tasks))
    plan = engine.plan({"score": 50.0})
    assert plan.total == 4
    assert [t.title for t in plan.all_tasks] == ["A", "B", "C", "D"]


# ---------------------------------------------------------------------------
# RemediationEngine — deduplication
# ---------------------------------------------------------------------------


def test_engine_deduplicates_same_title():
    tasks = [
        _task("Fix layer violations", "critical"),
        _task("Fix Layer Violations", "high"),  # duplicate (case-insensitive)
        _task("Add unit tests", "medium"),
    ]
    engine = RemediationEngine(MockRemediationProvider(tasks))
    plan = engine.plan({"score": 50.0})
    assert plan.total == 2
    # First occurrence wins → should be in critical
    assert len(plan.critical) == 1


def test_engine_deduplicates_whitespace_variants():
    tasks = [
        _task("  Fix cycles  ", "high"),
        _task("fix cycles", "critical"),
    ]
    engine = RemediationEngine(MockRemediationProvider(tasks))
    plan = engine.plan({"score": 50.0})
    assert plan.total == 1


# ---------------------------------------------------------------------------
# RemediationEngine — provider failure handling
# ---------------------------------------------------------------------------


def test_engine_handles_provider_exception():
    engine = RemediationEngine(MockRemediationProvider(raises=RuntimeError("LLM down")))
    plan = engine.plan(
        {"score": 50.0, "violations": [{"layer": 1, "module": "x", "message": "err"}]}
    )
    assert plan.total == 0


# ---------------------------------------------------------------------------
# OpenAIRemediationProvider — success path
# ---------------------------------------------------------------------------


@pytest.fixture
def oai_provider():
    return OpenAIRemediationProvider(api_key="test-key")


def _mock_oai_response(tasks_payload: list[dict]) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"tasks": tasks_payload})}}]
    }
    mock_resp.raise_for_status.return_value = None
    return mock_resp


def test_openai_provider_success(oai_provider):
    payload = [
        {
            "title": "Eliminate circular imports",
            "description": "Break cycles in auth module",
            "priority": "critical",
            "effort_days": 5,
            "acceptance_criteria": ["No cycles in import graph", "CI passes"],
        }
    ]
    with patch("httpx.Client.post", return_value=_mock_oai_response(payload)):
        tasks = oai_provider.generate_tasks("Some context")

    assert len(tasks) == 1
    assert tasks[0].title == "Eliminate circular imports"
    assert tasks[0].priority == "critical"
    assert tasks[0].effort_days == 5
    assert len(tasks[0].acceptance_criteria) == 2


def test_openai_provider_missing_api_key():
    provider = OpenAIRemediationProvider(api_key="")
    with pytest.raises(RemediationUnavailableError):
        provider.generate_tasks("context")


def test_openai_provider_timeout(oai_provider):
    with patch("httpx.Client.post", side_effect=httpx.TimeoutException("Timeout")):
        with pytest.raises(RemediationUnavailableError):
            oai_provider.generate_tasks("context")


def test_openai_provider_rate_limit(oai_provider):
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    with patch("httpx.Client.post") as mock_post:
        mock_post.return_value = MagicMock(
            raise_for_status=MagicMock(
                side_effect=httpx.HTTPStatusError(
                    "429", request=MagicMock(), response=mock_resp
                )
            )
        )
        with pytest.raises(RemediationUnavailableError):
            oai_provider.generate_tasks("context")


def test_openai_provider_malformed_json(oai_provider):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "not json at all"}}]
    }
    mock_resp.raise_for_status.return_value = None
    with patch("httpx.Client.post", return_value=mock_resp):
        with pytest.raises(RemediationUnavailableError):
            oai_provider.generate_tasks("context")


def test_openai_provider_invalid_task_fields_skipped(oai_provider):
    """Tasks missing title or description are skipped; valid ones are kept."""
    payload = [
        {
            "title": "Valid task",
            "description": "Do something",
            "priority": "high",
            "effort_days": 2,
            "acceptance_criteria": ["AC"],
        },
        {
            "title": "",
            "description": "Missing title",
            "priority": "high",
            "effort_days": 1,
            "acceptance_criteria": [],
        },
        {
            "description": "Missing title key",
            "priority": "low",
            "effort_days": 1,
            "acceptance_criteria": [],
        },
    ]
    with patch("httpx.Client.post", return_value=_mock_oai_response(payload)):
        tasks = oai_provider.generate_tasks("context")
    assert len(tasks) == 1
    assert tasks[0].title == "Valid task"


def test_openai_provider_clamps_effort_days(oai_provider):
    """effort_days below 1 is clamped to 1; non-integer falls back to 1."""
    payload = [
        {
            "title": "T1",
            "description": "D",
            "priority": "low",
            "effort_days": -5,
            "acceptance_criteria": [],
        },
        {
            "title": "T2",
            "description": "D",
            "priority": "low",
            "effort_days": "not-an-int",
            "acceptance_criteria": [],
        },
    ]
    with patch("httpx.Client.post", return_value=_mock_oai_response(payload)):
        tasks = oai_provider.generate_tasks("context")
    assert all(t.effort_days == 1 for t in tasks)


def test_openai_provider_bad_priority_falls_back_to_medium(oai_provider):
    payload = [
        {
            "title": "T",
            "description": "D",
            "priority": "galaxy-brain",
            "effort_days": 3,
            "acceptance_criteria": [],
        }
    ]
    with patch("httpx.Client.post", return_value=_mock_oai_response(payload)):
        tasks = oai_provider.generate_tasks("context")
    assert tasks[0].priority == "medium"


# ---------------------------------------------------------------------------
# End-to-end: engine + OpenAI provider integrated
# ---------------------------------------------------------------------------


def test_full_pipeline_violation_to_plan():
    payload = [
        {
            "title": "Fix auth violations",
            "description": "Refactor auth imports",
            "priority": "critical",
            "effort_days": 4,
            "acceptance_criteria": ["Zero layer violations in auth", "CI green"],
        },
        {
            "title": "Add integration tests",
            "description": "Cover boundary interactions",
            "priority": "medium",
            "effort_days": 3,
            "acceptance_criteria": ["80% coverage on boundary modules"],
        },
    ]

    provider = OpenAIRemediationProvider(api_key="test-key")
    with patch("httpx.Client.post", return_value=_mock_oai_response(payload)):
        plan = RemediationEngine(provider).plan(
            {
                "score": 45.0,
                "band": "FAIL",
                "violations": [
                    {
                        "layer": 1,
                        "module": "auth",
                        "message": "Illegal import",
                        "severity": "critical",
                    }
                ],
            }
        )

    assert plan.total == 2
    assert len(plan.critical) == 1
    assert len(plan.medium) == 1
    assert plan.critical[0].acceptance_criteria == [
        "Zero layer violations in auth",
        "CI green",
    ]

def test_remediation_too_many_violations_returns_422() -> None:
    """
    Regression test for MED-004.
    Verifies: a remediation request with 51 violations is rejected.
    """
    from fastapi.testclient import TestClient
    from archguard.dashboard.app import app
    client = TestClient(app)

    resp = client.post(
        "/api/remediation/plan",
        json={"violations": [{"id": str(i)} for i in range(51)]},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_remediation_job_id_filter(monkeypatch):
    import tempfile, pathlib
    from archguard.audit.logger import AuditLogger
    from archguard.dashboard.routes import remediation

    # This test exercises the job_id filtering in remediation_plan_from_audit.
    # Under CI's ARCHGUARD_MOCK_LLM=1 the endpoint returns a canned dict before
    # reading the audit log at all, so the filter would never run.
    monkeypatch.delenv("ARCHGUARD_MOCK_LLM", raising=False)

    with tempfile.TemporaryDirectory() as d:
        log_path = pathlib.Path(d) / "audit.jsonl"
        logger = AuditLogger(log_path)
        logger.log("analysis_run", job_id="job-A", timestamp="2026-01-01T00:00:00Z", violations=[{"id": "v1"}])
        logger.log("analysis_run", job_id="job-B", timestamp="2026-01-02T00:00:00Z", violations=[{"id": "v2"}])
        
        monkeypatch.setattr(remediation, "get_audit_path", lambda jid: log_path)
        
        # Capture what the endpoint actually forwards. The mock returns the real
        # function's shape (a dict with "tasks"), not the violations list: the
        # route now attaches selection metadata to that result, so a mock
        # returning a bare list would only be testing the error path.
        forwarded: dict[str, object] = {}

        async def mock_gen(violations, fitness_failures=None):
            forwarded["violations"] = violations
            forwarded["fitness_failures"] = fitness_failures
            return {"tasks": []}

        monkeypatch.setattr("archguard.llm.remediation.generate_remediation_plan", mock_gen)

        res = await remediation.remediation_plan_from_audit(limit=1, job_id="job-A")

        # job-A's violation is the one forwarded, not job-B's.
        assert [v["id"] for v in forwarded["violations"]] == ["v1"]
        assert res["selection"]["detected"] == 1
