"""Unit tests for archguard.llm.local."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from archguard.config import EVENT_LOCAL_LLM_FAILURE
from archguard.llm.local import (
    OLLAMA_DEFAULT_MODEL,
    OLLAMA_TIMEOUT,
    LocalLLMExplainer,
    LocalLLMFailureType,
)


class TestLocalLLMExplainer:
    """Tests for LocalLLMExplainer."""

    def test_connection_refused_not_installed(self) -> None:
        """Connection refused -> failure_type=NOT_INSTALLED, actionable message."""
        mock_audit = MagicMock()
        explainer = LocalLLMExplainer(audit_logger=mock_audit)

        with patch("archguard.llm.local.httpx.post") as mock_post:
            mock_post.side_effect = httpx.ConnectError("Connection refused")
            result = explainer.explain("test prompt")

        assert result.success is False
        assert result.failure_type == LocalLLMFailureType.NOT_INSTALLED
        assert "ollama serve" in result.failure_message

    def test_http_404_model_not_pulled(self) -> None:
        """HTTP 404 -> failure_type=MODEL_NOT_PULLED, message contains 'ollama pull'."""
        mock_audit = MagicMock()
        explainer = LocalLLMExplainer(audit_logger=mock_audit)

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "model not found"

        with patch("archguard.llm.local.httpx.post") as mock_post:
            mock_post.return_value = mock_response
            result = explainer.explain("test prompt")

        assert result.success is False
        assert result.failure_type == LocalLLMFailureType.MODEL_NOT_PULLED
        assert f"ollama pull {OLLAMA_DEFAULT_MODEL}" in result.failure_message

    def test_http_500_unexpected(self) -> None:
        """HTTP 500 -> failure_type=None (unexpected), message contains status code."""
        explainer = LocalLLMExplainer()

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "internal server error"

        with patch("archguard.llm.local.httpx.post") as mock_post:
            mock_post.return_value = mock_response
            result = explainer.explain("test prompt")

        assert result.success is False
        assert result.failure_type is None
        assert "500" in result.failure_message

    def test_success_200(self) -> None:
        """Success 200 -> LocalLLMResult(success=True, text=response)."""
        explainer = LocalLLMExplainer()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": "The violation explanation is...",
        }

        with patch("archguard.llm.local.httpx.post") as mock_post:
            mock_post.return_value = mock_response
            result = explainer.explain("test prompt")

        assert result.success is True
        assert result.text == "The violation explanation is..."

    def test_is_available_true(self) -> None:
        """is_available() -> True on 200 from /api/tags."""
        explainer = LocalLLMExplainer()

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("archguard.llm.local.httpx.get") as mock_get:
            mock_get.return_value = mock_response
            assert explainer.is_available() is True

    def test_is_available_false_on_error(self) -> None:
        """is_available() -> False on connection error, never raises."""
        explainer = LocalLLMExplainer()

        with patch("archguard.llm.local.httpx.get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("refused")
            assert explainer.is_available() is False

    def test_failure_logged_to_audit(self) -> None:
        """EVENT_LOCAL_LLM_FAILURE logged on all failure paths."""
        mock_audit = MagicMock()
        explainer = LocalLLMExplainer(audit_logger=mock_audit)

        with patch("archguard.llm.local.httpx.post") as mock_post:
            mock_post.side_effect = httpx.ConnectError("refused")
            explainer.explain("test")

        mock_audit.log.assert_called_once()
        assert mock_audit.log.call_args[0][0] == EVENT_LOCAL_LLM_FAILURE

    def test_timeout_respected(self) -> None:
        """Timeout (120s) is passed to httpx.post."""
        explainer = LocalLLMExplainer()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"response": "ok"}

        with patch("archguard.llm.local.httpx.post") as mock_post:
            mock_post.return_value = mock_response
            explainer.explain("test")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["timeout"] == OLLAMA_TIMEOUT
