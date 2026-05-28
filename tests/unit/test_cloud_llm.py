"""Unit tests for archguard.llm.cloud."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch, call

import pytest

from archguard.analysis.layers import AnalysisResult, ViolationDetail
from archguard.analysis.scoring import ArchDebtResult, LayerScores
from archguard.config import EVENT_TRUNCATED_EXPLANATION
from archguard.llm.cloud import (
    FALLBACK_MODEL,
    PRIMARY_MODEL,
    CloudLLMExplainer,
    LLMExplanationResult,
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
            layer=1, module="payments",
            message="Imports `auth.internal` (disallowed)",
            commit_sha="a1b2c3d", file_path="payments/views.py",
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
    "schema_version": "3.0",
    "modules": [
        {"name": "payments", "paths": ["payments/"]},
        {"name": "auth", "paths": ["auth/"]},
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
            mock_call.side_effect = [
                Exception("rate limited"),
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
            mock_call.side_effect = Exception("API down")
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
                layer=1, module="core",
                message=f"Token {pat} found in import",
                commit_sha="a1b2c3d", file_path="core/main.py",
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
            api_key="test-key", audit_logger=mock_audit,
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
            mock_call.side_effect = Exception("boom")
            llm_result = explainer.explain(result, _CONTRACT)

        # Should not raise
        assert llm_result.unavailable is True


class TestParseLLMResponse:
    """Tests for parse_llm_response."""

    def test_fewer_explanations_padded(self) -> None:
        """3 violations, 2 numbered responses -> third padded."""
        response = "1. First fix.\n2. Second fix."
        result = parse_llm_response(response, 3)
        assert len(result) == 3
        assert result[2] == "Explanation unavailable."
