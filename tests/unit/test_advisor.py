"""Unit tests for the Architecture Advisor foundation (Phase 3 Step 12)."""

from archguard.llm.advisor import ArchitectureAdvisor, AdvisorProvider, Recommendation


class MockProvider(AdvisorProvider):
    def __init__(self, expected_recs: list[Recommendation] | None = None):
        self.expected_recs = expected_recs or []
        self.last_context = ""

    def generate_recommendations(self, context: str) -> list[Recommendation]:
        self.last_context = context
        return self.expected_recs


def test_advisor_empty_history():
    """Verify advisor handles empty run history gracefully."""
    provider = MockProvider()
    advisor = ArchitectureAdvisor(provider)
    recs = advisor.analyze([])
    assert recs == []
    assert provider.last_context == ""


def test_advisor_builds_context_correctly():
    """Verify context string is built correctly with full data."""
    provider = MockProvider()
    advisor = ArchitectureAdvisor(provider)

    runs = [
        {"score": 50.0},
        {
            "score": 75.5,
            "band": "WARN",
            "violations": [
                {
                    "layer": 1,
                    "module": "auth",
                    "message": "Illegal import",
                    "severity": "high",
                },
                {
                    "layer": 2,
                    "module": "utils",
                    "message": "Too complex",
                    "severity": "low",
                },
            ],
            "metrics": {
                "layer_scores": {"1": 60.0, "2": 90.0},
                "fitness_results": [
                    {"name": "No cycles", "passed": True},
                    {
                        "name": "Max DB connections",
                        "passed": False,
                        "evidence": "Found 15 connections",
                    },
                ],
            },
            "ci_failures": ["Build timed out"],
        },
    ]

    advisor.analyze(runs)
    ctx = provider.last_context

    assert "Current Health Score: 75.50 (Grade: WARN)" in ctx
    assert "Active Violations: 2" in ctx
    assert "- [L1] auth: Illegal import (high)" in ctx
    assert "Layer Scores:" in ctx
    assert "- Layer 1: 60.00" in ctx
    assert "Fitness Function Results:" in ctx
    assert "- No cycles: PASS" in ctx
    assert "- Max DB connections: FAIL" in ctx
    assert "Evidence: Found 15 connections" in ctx
    assert "CI Failures:" in ctx
    assert "- Build timed out" in ctx
    assert "Trend: improving (from 50.00 to 75.50 over 2 runs)" in ctx


def test_advisor_handles_missing_fitness_and_ci():
    """Verify context string works when fitness or ci data is missing."""
    provider = MockProvider()
    advisor = ArchitectureAdvisor(provider)

    runs = [{"score": 90.0, "band": "PASS"}]

    advisor.analyze(runs)
    ctx = provider.last_context

    assert "Current Health Score: 90.00 (Grade: PASS)" in ctx
    assert "Active Violations: 0" in ctx
    assert "Fitness Function Results:" not in ctx
    assert "CI Failures:" not in ctx
    assert "Trend:" not in ctx  # Only 1 run


def test_advisor_prioritization_logic():
    """Verify recommendations are ranked correctly by severity and impact score."""
    rec1 = Recommendation("Low Sev High Score", "", "low", "", 99)
    rec2 = Recommendation("Critical Sev Low Score", "", "critical", "", 10)
    rec3 = Recommendation("High Sev Med Score", "", "high", "", 50)
    rec4 = Recommendation("High Sev High Score", "", "high", "", 80)

    provider = MockProvider([rec1, rec2, rec3, rec4])
    advisor = ArchitectureAdvisor(provider)

    runs = [{"score": 50.0}]
    results = advisor.analyze(runs)

    assert len(results) == 4
    # Expected order:
    # 1. Critical Sev Low Score (4000 + 10 = 4010)
    # 2. High Sev High Score (3000 + 80 = 3080)
    # 3. High Sev Med Score (3000 + 50 = 3050)
    # 4. Low Sev High Score (1000 + 99 = 1099)
    assert results[0].title == "Critical Sev Low Score"
    assert results[1].title == "High Sev High Score"
    assert results[2].title == "High Sev Med Score"
    assert results[3].title == "Low Sev High Score"


def test_advisor_prioritization_stable_sort():
    """Verify recommendations with same priority preserve their original order."""
    rec1 = Recommendation("First", "", "medium", "", 50)
    rec2 = Recommendation("Second", "", "medium", "", 50)

    provider = MockProvider([rec1, rec2])
    advisor = ArchitectureAdvisor(provider)

    runs = [{"score": 50.0}]
    results = advisor.analyze(runs)

    assert results[0].title == "First"
    assert results[1].title == "Second"

def test_advisor_stream_uses_the_configured_gemini_client(monkeypatch) -> None:
    """ask_stream must go through GeminiClient with the configured credentials.

    Note the scope change from the original MED-03 regression: the Advisor's
    model now comes from GEMINI_MODEL, not ARCHGUARD_PRIMARY_MODEL. The latter
    still exists but selects the primary tier for L4 explanations and contract
    inference, which is a different concern; model resolution itself is covered
    in tests/unit/test_gemini_client.py.
    """
    from unittest.mock import patch, MagicMock
    from archguard.llm.advisor import ArchitectureAdvisor

    # This test asserts on the real Gemini call, so the mock-LLM short-circuit
    # CI sets (ARCHGUARD_MOCK_LLM=1) must be off -- otherwise ask_stream returns
    # canned text and never reaches the client.
    monkeypatch.delenv('ARCHGUARD_MOCK_LLM', raising=False)
    monkeypatch.setenv('GEMINI_API_KEY', 'gemini-fake-testvalue')
    monkeypatch.setenv('GEMINI_MODEL', 'gemini-test-model-marker')

    mock_client = MagicMock()
    mock_client.stream.return_value = iter(['ok'])

    with patch('archguard.llm.gemini.GeminiClient', return_value=mock_client) as cls:
        advisor = ArchitectureAdvisor.__new__(ArchitectureAdvisor)
        assert list(advisor.ask_stream('question')) == ['ok']
        # The model is resolved by GeminiClient from GEMINI_MODEL.
        assert cls.call_args[1]['api_key'] == 'gemini-fake-testvalue'


def test_ask_stream_redacts_secrets_before_llm_call(monkeypatch) -> None:
    """
    Regression test for MED-005 (ported from the removed session advisor API).
    Verifies: an API-key-shaped string in the question or context is redacted
    before it appears in the prompt sent to Gemini.
    """
    from unittest.mock import patch, MagicMock
    from archguard.llm.advisor import ArchitectureAdvisor

    monkeypatch.delenv('ARCHGUARD_MOCK_LLM', raising=False)
    monkeypatch.setenv('GEMINI_API_KEY', 'gemini-fake-testvalue')

    mock_client = MagicMock()
    mock_client.stream.return_value = iter(['ok'])

    fake_key = 'sk-ant-api03-' + 'F' * 85

    with patch('archguard.llm.gemini.GeminiClient', return_value=mock_client):
        advisor = ArchitectureAdvisor.__new__(ArchitectureAdvisor)
        list(advisor.ask_stream(f'my key is {fake_key}', context=f'config: {fake_key}'))
        sent_text = mock_client.stream.call_args[0][0]
        assert fake_key not in sent_text

