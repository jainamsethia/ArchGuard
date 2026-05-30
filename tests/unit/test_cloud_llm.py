"""Unit tests for archguard.llm.cloud."""

from __future__ import annotations

from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from archguard.analysis.layers import AnalysisResult, ViolationDetail
from archguard.analysis.scoring import ArchDebtResult, LayerScores
from archguard.config import EVENT_TRUNCATED_EXPLANATION
from archguard.llm.cloud import (
    FALLBACK_MODEL,
    PRIMARY_MODEL,
    CloudLLMExplainer,
)
from archguard.llm.prompts import parse_llm_response


def _make_result(violations: list[ViolationDetail] | None = None) -> AnalysisResult:
    """Build a minimal AnalysisResult for testing."""
    scores = LayerScores(0.5, 0.3, 0.2, 0.1)
    archdebt = ArchDebtResult(
        composite_score=0.80,
        band=MagicMock(),
        layer_scores=scores,
        weights=(0.25, 0.25, 0.25, 0.25),
        per_component_breach=True,
        composite_breach=True,
        should_fail_ci=True,
    )
    viols = violations or [
        ViolationDetail(
            layer=1,
            module="payments",
            message="Imports `auth.internal` (disallowed)",
            commit_sha="a1b2c3d",
            file_path="payments/views.py",
        ),
    ]
    return AnalysisResult(
        archdebt=archdebt,
        violations=viols,
        layer_scores=scores,
        modules_analyzed=2,
        changed_files=["a.py", "b.py"],
        commit_sha="a1b2c3d",
    )


_CONTRACT: dict = {
    "version": "3.0",
    "modules": [
        {"name": "payments", "path": "payments/"},
        {"name": "auth", "path": "auth/"},
    ],
}


class TestCloudLLMExplainer:
    """Tests for CloudLLMExplainer."""

    def test_primary_model_success(self) -> None:
        """Primary model success -> unavailable=False, model_used=PRIMARY_MODEL."""
        explainer = CloudLLMExplainer(api_key="test-key")
        result = _make_result()

        with patch.object(explainer, "_call_api") as mock_call:
            mock_call.return_value = (
                "1. The payments module should not import auth internals. "
                "Move shared types to a common package.",
                "end_turn",
            )
            llm_result = explainer.explain(result, _CONTRACT)

        assert llm_result.unavailable is False
        assert llm_result.model_used == PRIMARY_MODEL
        assert len(llm_result.explanations) == 1

    def test_primary_fails_fallback_succeeds(self) -> None:
        """Primary fails, fallback succeeds -> model_used=FALLBACK_MODEL."""
        explainer = CloudLLMExplainer(api_key="test-key")
        result = _make_result()

        with patch.object(explainer, "_call_api") as mock_call:
            from archguard.utils.errors import LLMError

            mock_call.side_effect = [
                LLMError("rate limited"),
                ("1. Fix the import boundary.", "end_turn"),
            ]
            llm_result = explainer.explain(result, _CONTRACT)

        assert llm_result.unavailable is False
        assert llm_result.model_used == FALLBACK_MODEL

    def test_both_models_fail(self) -> None:
        """Both models fail -> unavailable=True, explanations=[]."""
        explainer = CloudLLMExplainer(api_key="test-key")
        result = _make_result()

        with patch.object(explainer, "_call_api") as mock_call:
            from archguard.utils.errors import LLMError

            mock_call.side_effect = LLMError("API down")
            llm_result = explainer.explain(result, _CONTRACT)

        assert llm_result.unavailable is True
        assert llm_result.explanations == []

    def test_stop_reason_max_tokens_truncated(self) -> None:
        """stop_reason='max_tokens' -> truncated=True, truncation note appended."""
        explainer = CloudLLMExplainer(api_key="test-key")
        result = _make_result()

        with patch.object(explainer, "_call_api") as mock_call:
            mock_call.return_value = (
                "1. The payments module crosses the auth boundary which",
                "max_tokens",
            )
            llm_result = explainer.explain(result, _CONTRACT)

        assert llm_result.truncated is True
        assert "[Note: explanation was truncated]" in llm_result.explanations[-1]

    def test_end_turn_with_period_not_truncated(self) -> None:
        """stop_reason='end_turn', ends with '.' -> truncated=False."""
        explainer = CloudLLMExplainer(api_key="test-key")
        result = _make_result()

        with patch.object(explainer, "_call_api") as mock_call:
            mock_call.return_value = (
                "1. Move shared auth types to a common module.",
                "end_turn",
            )
            llm_result = explainer.explain(result, _CONTRACT)

        assert llm_result.truncated is False

    def test_end_turn_no_terminal_punct_truncated(self) -> None:
        """stop_reason='end_turn', ends with 'word' -> truncated=True."""
        explainer = CloudLLMExplainer(api_key="test-key")
        result = _make_result()

        with patch.object(explainer, "_call_api") as mock_call:
            mock_call.return_value = (
                "1. Move shared auth types to a common",
                "end_turn",
            )
            llm_result = explainer.explain(result, _CONTRACT)

        assert llm_result.truncated is True

    def test_secrets_redacted_before_api_call(self) -> None:
        """Violations with secrets -> secrets redacted before API call."""
        pat = "ghp_" + "x" * 36
        violations = [
            ViolationDetail(
                layer=1,
                module="core",
                message=f"Token {pat} found in import",
                commit_sha="a1b2c3d",
                file_path="core/main.py",
            ),
        ]
        explainer = CloudLLMExplainer(api_key="test-key")
        result = _make_result(violations)

        with patch.object(explainer, "_call_api") as mock_call:
            mock_call.return_value = ("1. Redact tokens.", "end_turn")
            explainer.explain(result, _CONTRACT)

        # Check that the prompt passed to _call_api does NOT contain the raw PAT
        called_prompt = mock_call.call_args[0][0]
        assert pat not in called_prompt
        assert "[REDACTED:GITHUB_PAT]" in called_prompt

    def test_truncation_logged_to_audit(self) -> None:
        """EVENT_TRUNCATED_EXPLANATION logged when truncated=True."""
        mock_audit = MagicMock()
        explainer = CloudLLMExplainer(
            api_key="test-key",
            audit_logger=mock_audit,
        )
        result = _make_result()

        with patch.object(explainer, "_call_api") as mock_call:
            mock_call.return_value = ("1. Fix boundary", "max_tokens")
            explainer.explain(result, _CONTRACT)

        mock_audit.log.assert_called_once()
        assert mock_audit.log.call_args[0][0] == EVENT_TRUNCATED_EXPLANATION

    def test_unavailable_does_not_raise(self) -> None:
        """LLM unavailable -> returns result, does not raise."""
        explainer = CloudLLMExplainer(api_key="test-key")
        result = _make_result()

        with patch.object(explainer, "_call_api") as mock_call:
            from archguard.utils.errors import LLMError

            mock_call.side_effect = LLMError("boom")
            llm_result = explainer.explain(result, _CONTRACT)

        # Should not raise
        assert llm_result.unavailable is True

    def test_batch_chunking_and_fallback(self) -> None:
        """With 5 violations, exactly 1 API call is made. If batch fails, fallback to 5 individual calls."""
        explainer = CloudLLMExplainer(api_key="test-key")

        viols = [
            ViolationDetail(
                1,
                module=f"mod{i}",
                message=f"msg{i}",
                commit_sha="abc",
                file_path=f"f{i}.py",
            )
            for i in range(5)
        ]
        result = _make_result(viols)

        # Scenario 1: Success
        with patch.object(explainer, "_call_api") as mock_call:
            mock_call.return_value = ("1. E1\n2. E2\n3. E3\n4. E4\n5. E5", "end_turn")
            llm_result = explainer.explain(result, _CONTRACT)

            assert mock_call.call_count == 1
            assert len(llm_result.explanations) == 5

        # Scenario 2: Batch fails, fallbacks to individual
        with patch.object(explainer, "_call_api") as mock_call:

            def side_effect(prompt, model):
                if "mod0" in prompt and "mod4" in prompt:
                    from archguard.utils.errors import LLMError

                    raise LLMError("batch failed")
                return ("1. Individual explanation.", "end_turn")

            mock_call.side_effect = side_effect
            llm_result = explainer.explain(result, _CONTRACT)

            # 1 batch call * 2 models = 2 calls
            # Then 5 individual items * 1 model each (since primary succeeds) = 5 calls
            # Total calls = 7
            assert mock_call.call_count == 7
            assert len(llm_result.explanations) == 5

    @pytest.mark.asyncio
    async def test_concurrent_explainer_uses_configured_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the model sent in API calls matches the configured FALLBACK_MODEL."""
        monkeypatch.setattr("archguard.llm.cloud.FALLBACK_MODEL", "test-model-123")
        monkeypatch.setattr("archguard.llm.cloud._ML_AVAILABLE", True)

        explainer = CloudLLMExplainer(api_key="test-key")
        result = _make_result()

        # mock anthropic AsyncAnthropic client
        mock_client = MagicMock()
        mock_messages = AsyncMock()
        mock_message_resp = MagicMock()
        mock_message_resp.content = [MagicMock(text="response text")]
        mock_messages.create.return_value = mock_message_resp
        mock_client.messages = mock_messages

        mock_async_client_cls = MagicMock()
        mock_async_client_cls.return_value.__aenter__.return_value = mock_client
        mock_async_client_cls.return_value.__aexit__ = AsyncMock()

        monkeypatch.setattr(
            "archguard.llm.cloud.anthropic.AsyncAnthropic", mock_async_client_cls
        )

        await explainer.explain_violations_concurrent(
            result.violations, _CONTRACT, result.changed_files
        )

        mock_messages.create.assert_called_once()
        assert mock_messages.create.call_args[1]["model"] == "test-model-123"


class TestParseLLMResponse:
    """Tests for parse_llm_response."""

    def test_fewer_explanations_padded(self) -> None:
        """3 violations, 2 numbered responses -> third padded."""
        response = "1. First fix.\n2. Second fix."
        result = parse_llm_response(response, 3)
        assert len(result) == 3
        assert result[2] == "Explanation unavailable."
