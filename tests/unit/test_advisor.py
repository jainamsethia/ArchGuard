"""Unit tests for the Architecture Advisor streaming path.

The provider-abstraction tests that lived here covered AdvisorProvider /
Recommendation / analyze(), which nothing in production ever instantiated.
They were removed with the code they exercised.
"""


def test_advisor_stream_uses_the_configured_gemini_client(monkeypatch) -> None:
    """ask_stream must go through GeminiClient with the configured credentials.

    Note the scope change from the original MED-03 regression: the Advisor's
    model now comes from GEMINI_MODEL, not ARCHGUARD_PRIMARY_MODEL. The latter
    still exists but selects the primary tier for L4 explanations and contract
    inference, which is a different concern; model resolution itself is covered
    in tests/unit/test_gemini_client.py.
    """
    from unittest.mock import MagicMock, patch

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
    from unittest.mock import MagicMock, patch

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

