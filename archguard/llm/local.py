"""Ollama HTTP client for local LLM explanations."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, TYPE_CHECKING

import httpx

from archguard.config import EVENT_LOCAL_LLM_FAILURE
from archguard.utils.errors import LLMError
from archguard.utils.retry import with_retry

if TYPE_CHECKING:
    from archguard.audit.logger import AuditLogger

logger: logging.Logger = logging.getLogger(__name__)

OLLAMA_DEFAULT_URL: str = "http://localhost:11434"
OLLAMA_DEFAULT_MODEL: str = "llama3"
OLLAMA_TIMEOUT: float = 120.0


class LocalLLMFailureType(str, Enum):
    """Failure types for local LLM calls."""

    NOT_INSTALLED = "NOT_INSTALLED"
    NOT_RUNNING = "NOT_RUNNING"
    MODEL_NOT_PULLED = "MODEL_NOT_PULLED"


@dataclass
class LocalLLMResult:
    """Result of a local LLM call."""

    text: str
    model: str
    success: bool
    failure_type: LocalLLMFailureType | None
    failure_message: str


class LocalLLMExplainer:
    """Calls Ollama for local LLM explanations."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._base_url: str = base_url or os.environ.get(
            "OLLAMA_BASE_URL", OLLAMA_DEFAULT_URL
        )
        self._model: str = model or os.environ.get("OLLAMA_MODEL", OLLAMA_DEFAULT_MODEL)
        self._audit: AuditLogger | None = audit_logger

    @with_retry(max_attempts=3, retryable_exceptions=(Exception,))
    def _call_api(self, url: str, prompt: str) -> httpx.Response:
        try:
            return httpx.post(
                url,
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "stream": False,
                },
                headers={"Content-Type": "application/json"},
                timeout=OLLAMA_TIMEOUT,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise
        except Exception as e:
            raise LLMError("Unexpected error connecting to ollama", cause=e) from e

    def explain(self, prompt: str) -> LocalLLMResult:
        """Send a prompt to Ollama and return the result.

        Handles three failure paths with actionable error messages.
        Never raises — always returns a ``LocalLLMResult``.
        """
        url = f"{self._base_url}/api/generate"

        try:
            response = self._call_api(url, prompt)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            failure_msg = (
                f"ollama is not running at {self._base_url}. "
                f"Start it with: ollama serve"
            )
            self._log_failure(LocalLLMFailureType.NOT_INSTALLED, failure_msg)
            return LocalLLMResult(
                text="",
                model=self._model,
                success=False,
                failure_type=LocalLLMFailureType.NOT_INSTALLED,
                failure_message=failure_msg,
            )
        except LLMError as exc:
            failure_msg = f"Unexpected error connecting to ollama: {exc.cause}"
            self._log_failure(None, failure_msg)
            return LocalLLMResult(
                text="",
                model=self._model,
                success=False,
                failure_type=None,
                failure_message=failure_msg,
            )

        if response.status_code == 404:
            failure_msg = (
                f"ollama model '{self._model}' is not available. "
                f"Pull it with: ollama pull {self._model}"
            )
            self._log_failure(LocalLLMFailureType.MODEL_NOT_PULLED, failure_msg)
            return LocalLLMResult(
                text="",
                model=self._model,
                success=False,
                failure_type=LocalLLMFailureType.MODEL_NOT_PULLED,
                failure_message=failure_msg,
            )

        if response.status_code != 200:
            resp_text = response.text[:200]
            failure_msg = f"ollama returned HTTP {response.status_code}: {resp_text}"
            self._log_failure(None, failure_msg)
            return LocalLLMResult(
                text="",
                model=self._model,
                success=False,
                failure_type=None,
                failure_message=failure_msg,
            )

        # Success
        try:
            data: dict[str, Any] = response.json()
            text = str(data.get("response", ""))
        except Exception as e:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).warning(
                f"Non-critical failure in local llm parse: {e}"
            )
            text = response.text

        return LocalLLMResult(
            text=text,
            model=self._model,
            success=True,
            failure_type=None,
            failure_message="",
        )

    def is_available(self) -> bool:
        """Check if Ollama is running by hitting ``/api/tags``.

        Returns ``True`` on HTTP 200, ``False`` otherwise. Never raises.
        """
        try:
            response = httpx.get(
                f"{self._base_url}/api/tags",
                timeout=2.0,
            )
            return bool(response.status_code == 200)
        except Exception as e:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).warning(
                f"Non-critical failure in local llm is_available: {e}"
            )
            return False

    def _log_failure(
        self,
        failure_type: LocalLLMFailureType | None,
        message: str,
    ) -> None:
        """Log failure to audit logger if available."""
        logger.warning("Local LLM failure: %s", message)
        if self._audit:
            self._audit.log(
                EVENT_LOCAL_LLM_FAILURE,
                failure_type=failure_type.value if failure_type else "UNKNOWN",
                message=message,
            )
