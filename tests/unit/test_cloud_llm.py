"""Unit tests for archguard.llm.cloud (concurrent explainer API)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from archguard.analysis.layers import AnalysisResult, ViolationDetail
from archguard.analysis.scoring import ArchDebtResult, LayerScores
from archguard.config import EVENT_TRUNCATED_EXPLANATION
from archguard.llm.cloud import (
    FALLBACK_MODEL,
    MAX_TOKENS,
    PRIMARY_MODEL,
    CloudLLMExplainer,
)
from archguard.llm.gemini import (
    NON_RETRYABLE_ERRORS,
    RETRYABLE_ERRORS,
    TRY_NEXT_MODEL_ERRORS,
    GeminiAuthError,
    GeminiModelNotFoundError,
    GeminiRateLimitError,
    GeminiServerError,
    _raise_for_status,
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


def _mock_gemini(monkeypatch: pytest.MonkeyPatch, acomplete: AsyncMock) -> None:
    """Install a fake GeminiClient whose acomplete() is *acomplete*."""
    fake_client = MagicMock()
    fake_client.acomplete = acomplete
    monkeypatch.setattr(
        "archguard.llm.cloud.GeminiClient", MagicMock(return_value=fake_client)
    )
    monkeypatch.delenv("ARCHGUARD_MOCK_LLM", raising=False)


def _response(text: str, finish_reason: str = "stop") -> tuple[str, str]:
    """GeminiClient.acomplete returns (text, finish_reason)."""
    return text, finish_reason


class TestCloudLLMExplainer:
    """Tests for CloudLLMExplainer.explain_violations_concurrent."""

    @pytest.mark.asyncio
    async def test_primary_model_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Primary model succeeds -> its text is returned, one call with PRIMARY_MODEL."""
        create = AsyncMock(return_value=_response("1. Move shared types to a common package."))
        _mock_gemini(monkeypatch, create)

        explainer = CloudLLMExplainer(api_key="test-key")
        result = _make_result()

        out = await explainer.explain_violations_concurrent(
            result.violations, _CONTRACT, result.changed_files
        )

        assert out == ["1. Move shared types to a common package."]
        create.assert_called_once()
        assert create.call_args[1]["model"] == PRIMARY_MODEL
        assert create.call_args[1]["max_tokens"] == MAX_TOKENS

    @pytest.mark.asyncio
    async def test_primary_fails_fallback_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Primary raises -> fallback model is tried and its text returned."""
        create = AsyncMock(
            side_effect=[GeminiRateLimitError("rate limited"), _response("1. Fix the import boundary.")]
        )
        _mock_gemini(monkeypatch, create)

        explainer = CloudLLMExplainer(api_key="test-key")
        result = _make_result()

        out = await explainer.explain_violations_concurrent(
            result.violations, _CONTRACT, result.changed_files
        )

        assert out == ["1. Fix the import boundary."]
        assert create.call_count == 2
        assert create.call_args_list[0][1]["model"] == PRIMARY_MODEL
        assert create.call_args_list[1][1]["model"] == FALLBACK_MODEL

    @pytest.mark.asyncio
    async def test_both_models_fail_returns_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both models raise -> the per-violation result is the exception, not a crash."""
        create = AsyncMock(side_effect=GeminiServerError("API down"))
        _mock_gemini(monkeypatch, create)

        explainer = CloudLLMExplainer(api_key="test-key")
        result = _make_result()

        out = await explainer.explain_violations_concurrent(
            result.violations, _CONTRACT, result.changed_files
        )

        assert len(out) == 1
        assert isinstance(out[0], Exception)

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No API key -> clear 'unavailable' text per violation, no API calls."""
        create = AsyncMock()
        _mock_gemini(monkeypatch, create)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        explainer = CloudLLMExplainer(api_key="")
        result = _make_result()

        out = await explainer.explain_violations_concurrent(
            result.violations, _CONTRACT, result.changed_files
        )

        assert out == ["Explanation unavailable (GEMINI_API_KEY not set)"]
        create.assert_not_called()

    @pytest.mark.asyncio
    async def test_auth_failure_is_not_retried_on_the_fallback_tier(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bad credentials fail identically on both tiers.

        The previous implementation caught bare Exception, so an auth error
        burned an attempt on the fallback model and surfaced the second failure,
        hiding the real cause. Only transient errors should fall back.
        """
        create = AsyncMock(side_effect=GeminiAuthError("invalid key"))
        _mock_gemini(monkeypatch, create)

        explainer = CloudLLMExplainer(api_key="bad-key")
        result = _make_result()

        out = await explainer.explain_violations_concurrent(
            result.violations, _CONTRACT, result.changed_files
        )

        assert isinstance(out[0], GeminiAuthError)
        create.assert_called_once()
        assert create.call_args[1]["model"] == PRIMARY_MODEL

    @pytest.mark.asyncio
    async def test_secrets_redacted_before_api_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Violations with secrets -> secrets redacted from the prompt sent to the API."""
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
        create = AsyncMock(return_value=_response("1. Redact tokens."))
        _mock_gemini(monkeypatch, create)

        explainer = CloudLLMExplainer(api_key="test-key")
        result = _make_result(violations)

        await explainer.explain_violations_concurrent(
            result.violations, _CONTRACT, result.changed_files
        )

        sent_prompt = create.call_args[0][0]
        assert pat not in sent_prompt
        assert "[REDACTED:GITHUB_PAT]" in sent_prompt

    @pytest.mark.asyncio
    async def test_stop_reason_max_tokens_appends_truncation_note(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """stop_reason='max_tokens' -> truncation note appended to the explanation."""
        create = AsyncMock(
            return_value=_response("1. The payments module crosses the", "max_tokens")
        )
        _mock_gemini(monkeypatch, create)

        explainer = CloudLLMExplainer(api_key="test-key")
        result = _make_result()

        out = await explainer.explain_violations_concurrent(
            result.violations, _CONTRACT, result.changed_files
        )

        assert "[Note: explanation was truncated]" in out[0]

    @pytest.mark.asyncio
    async def test_end_turn_with_period_not_truncated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """stop_reason='end_turn' ending with '.' -> no truncation note."""
        create = AsyncMock(return_value=_response("1. Move shared auth types to a common module."))
        _mock_gemini(monkeypatch, create)

        explainer = CloudLLMExplainer(api_key="test-key")
        result = _make_result()

        out = await explainer.explain_violations_concurrent(
            result.violations, _CONTRACT, result.changed_files
        )

        assert "[Note: explanation was truncated]" not in out[0]

    @pytest.mark.asyncio
    async def test_end_turn_no_terminal_punct_truncated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """stop_reason='end_turn' without terminal punctuation -> truncation note."""
        create = AsyncMock(return_value=_response("1. Move shared auth types to a common"))
        _mock_gemini(monkeypatch, create)

        explainer = CloudLLMExplainer(api_key="test-key")
        result = _make_result()

        out = await explainer.explain_violations_concurrent(
            result.violations, _CONTRACT, result.changed_files
        )

        assert "[Note: explanation was truncated]" in out[0]

    @pytest.mark.asyncio
    async def test_truncation_logged_to_audit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """EVENT_TRUNCATED_EXPLANATION is audit-logged when a response is truncated."""
        create = AsyncMock(return_value=_response("1. Fix boundary", "max_tokens"))
        _mock_gemini(monkeypatch, create)

        mock_audit = MagicMock()
        explainer = CloudLLMExplainer(api_key="test-key", audit_logger=mock_audit)
        result = _make_result()

        await explainer.explain_violations_concurrent(
            result.violations, _CONTRACT, result.changed_files
        )

        mock_audit.log.assert_called_once()
        assert mock_audit.log.call_args[0][0] == EVENT_TRUNCATED_EXPLANATION

    @pytest.mark.asyncio
    async def test_concurrent_explainer_uses_configured_fallback_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the primary fails, the configured FALLBACK_MODEL is what gets called."""
        monkeypatch.setattr("archguard.llm.cloud.FALLBACK_MODEL", "test-model-123")
        create = AsyncMock(
            side_effect=[GeminiRateLimitError("primary down"), _response("response text.")]
        )
        _mock_gemini(monkeypatch, create)

        explainer = CloudLLMExplainer(api_key="test-key")
        result = _make_result()

        await explainer.explain_violations_concurrent(
            result.violations, _CONTRACT, result.changed_files
        )

        assert create.call_args[1]["model"] == "test-model-123"

    def test_missing_key_error_names_the_variable_to_set(self, monkeypatch):
        """The sync path must say which variable is missing, not fail opaquely."""
        import archguard.llm.cloud as cloud_module

        monkeypatch.delenv("ARCHGUARD_MOCK_LLM", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        explainer = cloud_module.CloudLLMExplainer(api_key="")
        with pytest.raises(GeminiAuthError, match="GEMINI_API_KEY"):
            explainer._call_api("prompt", "model")


class TestParseLLMResponse:
    """Tests for parse_llm_response."""

    def test_fewer_explanations_padded(self) -> None:
        """3 violations, 2 numbered responses -> third padded."""
        response = "1. First fix.\n2. Second fix."
        result = parse_llm_response(response, 3)
        assert len(result) == 3
        assert result[2] == "Explanation unavailable."


class TestRetiredModelFallsBack:
    """A retired model id must not take the fallback tier down with it (P2-8).

    Google retires model ids on a schedule, and this project has already been
    bitten: gemini.py records that the 2.x ids it once shipped are still listed
    in Google's documentation yet return 404 for newly issued keys. A 404 used
    to land in the generic 4xx bucket (GeminiResponseError, non-retryable), so
    the explainer re-raised at once and never tried the cheaper model -- the
    single failure the two tiers exist to survive.
    """

    def test_404_is_its_own_error_not_a_generic_bad_request(self) -> None:
        import httpx

        response = httpx.Response(
            404,
            text='{"error": {"message": "models/gemini-2.5-flash is not found"}}',
            request=httpx.Request("POST", "https://example.invalid"),
        )
        with pytest.raises(GeminiModelNotFoundError):
            _raise_for_status(response)

    def test_a_plain_bad_request_is_not_a_model_error(self) -> None:
        import httpx

        response = httpx.Response(
            400,
            text='{"error": {"message": "malformed"}}',
            request=httpx.Request("POST", "https://example.invalid"),
        )
        with pytest.raises(Exception) as caught:
            _raise_for_status(response)
        assert not isinstance(caught.value, GeminiModelNotFoundError)

    def test_model_errors_are_never_retried_on_the_same_model(self) -> None:
        # Asking twice for a model that does not exist just burns the budget.
        for err in TRY_NEXT_MODEL_ERRORS:
            assert err in NON_RETRYABLE_ERRORS
            assert err not in RETRYABLE_ERRORS

    @pytest.mark.asyncio
    async def test_retired_primary_falls_back_to_the_cheaper_tier(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        create = AsyncMock(
            side_effect=[
                GeminiModelNotFoundError("models/gemini-x is not found"),
                _response("1. Fix the import boundary."),
            ]
        )
        _mock_gemini(monkeypatch, create)

        result = _make_result()
        out = await CloudLLMExplainer(api_key="k").explain_violations_concurrent(
            result.violations, _CONTRACT, result.changed_files
        )

        assert out == ["1. Fix the import boundary."]
        assert create.call_count == 2, "the fallback tier was never tried"
        assert create.call_args_list[0][1]["model"] == PRIMARY_MODEL
        assert create.call_args_list[1][1]["model"] == FALLBACK_MODEL

    @pytest.mark.asyncio
    async def test_both_tiers_retired_reports_the_model_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        create = AsyncMock(side_effect=GeminiModelNotFoundError("no such model"))
        _mock_gemini(monkeypatch, create)

        result = _make_result()
        out = await CloudLLMExplainer(api_key="k").explain_violations_concurrent(
            result.violations, _CONTRACT, result.changed_files
        )

        assert isinstance(out[0], GeminiModelNotFoundError)
        assert create.call_count == 2

    @pytest.mark.asyncio
    async def test_widening_the_fallback_did_not_widen_it_for_bad_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The counterpart guarantee to the two tests above.
        create = AsyncMock(side_effect=GeminiAuthError("invalid key"))
        _mock_gemini(monkeypatch, create)

        result = _make_result()
        out = await CloudLLMExplainer(api_key="bad").explain_violations_concurrent(
            result.violations, _CONTRACT, result.changed_files
        )

        assert isinstance(out[0], GeminiAuthError)
        create.assert_called_once()
