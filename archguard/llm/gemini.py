"""Google Gemini client, ArchGuard's sole LLM provider.

Gemini is reached through its OpenAI-compatible endpoint rather than the
``google-genai`` SDK. That keeps one wire format across every call site, means
no vendor SDK in the dependency tree, and lets the same request/response shape
serve the advisor, remediation, L4 explanations and contract inference.

Configuration (all optional except the key):

``GEMINI_API_KEY``   the key. ``OPENAI_API_KEY`` is still read as a deprecated
                     alias so existing deployments do not break silently, but it
                     warns: with Gemini as the only provider, an OpenAI-shaped
                     name tells operators to provision the wrong credential.
``GEMINI_MODEL``     default model for one-shot calls.
``GEMINI_BASE_URL``  override for proxies or a future API version.

Errors are mapped onto a small typed hierarchy so callers can distinguish
"retry this" from "this will never work", which the previous Anthropic code got
from the SDK's exception classes.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator, Iterator

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_MODEL = "gemini-3.6-flash"

# Two tiers for the primary/fallback resilience pattern: a capable model first,
# a cheaper and faster one when the first is rate-limited or unavailable.
# Override if Google's model line-up moves on -- these are IDs, not guarantees.
#
# Both are documented as stable at
# https://ai.google.dev/gemini-api/docs/models. The 2.x IDs used previously are
# still listed there but returned 404 for at least one newly issued API key, so
# they are not a safe default for new projects.
DEFAULT_PRIMARY_MODEL = "gemini-3.6-flash"
DEFAULT_FALLBACK_MODEL = "gemini-3.5-flash-lite"

DEFAULT_TIMEOUT = 60.0
# Gemini 3.x are thinking models: reasoning tokens are drawn from this same
# budget before any visible output is emitted. Measured against a real key, a
# 1500-token budget yielded only 216 characters of visible JSON with
# finish_reason="length" -- the rest went on thinking. The old 2048 default was
# inherited from Anthropic, where no such reservation existed, and truncated
# structured responses mid-object. Sized generously; callers may override.
DEFAULT_MAX_TOKENS = 8192


class GeminiError(RuntimeError):
    """Base class for Gemini call failures."""


class GeminiAuthError(GeminiError):
    """Missing, invalid or unauthorised credentials. Never worth retrying."""


class GeminiRateLimitError(GeminiError):
    """Quota or rate limit hit. Worth retrying, or falling back to a cheaper tier."""


class GeminiServerError(GeminiError):
    """5xx from the API. Transient by nature."""


class GeminiConnectionError(GeminiError):
    """Network failure or timeout reaching the API."""


class GeminiResponseError(GeminiError):
    """A 2xx response whose body was not the shape we expect."""


# Exposed for callers wiring up retry policies (see archguard.utils.retry).
RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    GeminiRateLimitError,
    GeminiServerError,
    GeminiConnectionError,
)
NON_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    GeminiAuthError,
    GeminiResponseError,
    ValueError,
    TypeError,
)


def resolve_api_key(explicit: str | None = None) -> str:
    """Return the configured Gemini key, or "" when none is set."""
    if explicit:
        return explicit
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key
    legacy = os.environ.get("OPENAI_API_KEY", "")
    if legacy:
        logger.warning(
            "Using OPENAI_API_KEY for Gemini. This alias is deprecated -- set "
            "GEMINI_API_KEY instead; the name matters because Gemini is now the "
            "only provider and an OpenAI key will not work here."
        )
        return legacy
    return ""


def resolve_base_url(explicit: str | None = None) -> str:
    url = explicit or os.environ.get("GEMINI_BASE_URL") or DEFAULT_BASE_URL
    return url.rstrip("/")


def resolve_model(explicit: str | None = None) -> str:
    return explicit or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL


def primary_model() -> str:
    return os.environ.get("ARCHGUARD_PRIMARY_MODEL") or DEFAULT_PRIMARY_MODEL


def fallback_model() -> str:
    return os.environ.get("ARCHGUARD_FALLBACK_MODEL") or DEFAULT_FALLBACK_MODEL


def _raise_for_status(response: httpx.Response) -> None:
    """Map an HTTP status onto the typed hierarchy."""
    status = response.status_code
    if status < 400:
        return

    try:
        detail = response.text[:500]
    except Exception:  # noqa: BLE001 - detail is best-effort only
        detail = ""

    if status in (401, 403):
        raise GeminiAuthError(f"Gemini rejected the credentials ({status}): {detail}")
    if status == 429:
        retry_after = response.headers.get("Retry-After", "")
        suffix = f" Retry after {retry_after}s." if retry_after else ""
        raise GeminiRateLimitError(f"Gemini rate limit exceeded.{suffix} {detail}")
    if status >= 500:
        raise GeminiServerError(f"Gemini server error ({status}): {detail}")
    raise GeminiResponseError(f"Gemini request failed ({status}): {detail}")


def _extract_message(payload: dict[str, Any]) -> tuple[str, str]:
    """Pull (text, finish_reason) out of a chat-completions response."""
    try:
        choice = payload["choices"][0]
        content = choice["message"]["content"]
        finish = str(choice.get("finish_reason") or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiResponseError(f"Unexpected Gemini response shape: {exc}") from exc
    return str(content or ""), finish


def _delta_text(line: str) -> str | None:
    """Decode one SSE line into its incremental text, if it carries any.

    Streaming responses arrive as ``data: {json}`` lines terminated by
    ``data: [DONE]``. Anything else (comments, keep-alives, blank lines) is
    skipped rather than treated as an error.
    """
    if not line.startswith("data:"):
        return None
    body = line[len("data:") :].strip()
    if not body or body == "[DONE]":
        return None
    try:
        chunk = json.loads(body)
    except json.JSONDecodeError:
        logger.debug("Skipping unparseable stream chunk: %.120s", body)
        return None
    try:
        delta = chunk["choices"][0].get("delta") or {}
    except (KeyError, IndexError, TypeError):
        return None
    text = delta.get("content")
    return str(text) if text else None


class GeminiClient:
    """Thin client over Gemini's OpenAI-compatible chat completions API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = resolve_api_key(api_key)
        self.model = resolve_model(model)
        self.base_url = resolve_base_url(base_url)
        self.timeout = timeout

    # -- request construction ------------------------------------------------

    @property
    def _endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(
        self,
        messages: list[dict[str, str]],
        model: str | None,
        max_tokens: int,
        temperature: float,
        stream: bool,
        json_object: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if stream:
            payload["stream"] = True
        if json_object:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _require_key(self) -> None:
        if not self.api_key:
            raise GeminiAuthError(
                "GEMINI_API_KEY is not configured. Set it to enable AI features."
            )

    @staticmethod
    def _messages(system: str, user: str) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        return messages

    # -- synchronous ---------------------------------------------------------

    def complete(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.2,
        json_object: bool = False,
    ) -> tuple[str, str]:
        """One-shot completion. Returns ``(text, finish_reason)``."""
        self._require_key()
        payload = self._payload(
            self._messages(system, prompt), model, max_tokens, temperature,
            stream=False, json_object=json_object,
        )
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    self._endpoint, json=payload, headers=self._headers()
                )
        except httpx.TimeoutException as exc:
            raise GeminiConnectionError(f"Gemini request timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise GeminiConnectionError(f"Could not reach Gemini: {exc}") from exc

        _raise_for_status(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise GeminiResponseError("Gemini returned a non-JSON body") from exc
        return _extract_message(body)

    def stream(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.2,
    ) -> Iterator[str]:
        """Yield text fragments as they arrive.

        A real streaming HTTP request, not a one-shot call chopped up after the
        fact: the caller sees the first tokens as soon as Gemini emits them.
        """
        self._require_key()
        payload = self._payload(
            self._messages(system, prompt), model, max_tokens, temperature, stream=True
        )
        try:
            with httpx.Client(timeout=self.timeout) as client:
                with client.stream(
                    "POST", self._endpoint, json=payload, headers=self._headers()
                ) as response:
                    if response.status_code >= 400:
                        response.read()
                        _raise_for_status(response)
                    for line in response.iter_lines():
                        text = _delta_text(line)
                        if text:
                            yield text
        except httpx.TimeoutException as exc:
            raise GeminiConnectionError(f"Gemini stream timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise GeminiConnectionError(f"Could not reach Gemini: {exc}") from exc

    # -- asynchronous --------------------------------------------------------

    async def acomplete(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.2,
    ) -> tuple[str, str]:
        """Async one-shot completion, for the concurrent L4 explanation path."""
        self._require_key()
        payload = self._payload(
            self._messages(system, prompt), model, max_tokens, temperature, stream=False
        )
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self._endpoint, json=payload, headers=self._headers()
                )
        except httpx.TimeoutException as exc:
            raise GeminiConnectionError(f"Gemini request timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise GeminiConnectionError(f"Could not reach Gemini: {exc}") from exc

        _raise_for_status(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise GeminiResponseError("Gemini returned a non-JSON body") from exc
        return _extract_message(body)

    async def astream(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.2,
    ) -> AsyncIterator[str]:
        """Async variant of :meth:`stream`."""
        self._require_key()
        payload = self._payload(
            self._messages(system, prompt), model, max_tokens, temperature, stream=True
        )
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST", self._endpoint, json=payload, headers=self._headers()
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        _raise_for_status(response)
                    async for line in response.aiter_lines():
                        text = _delta_text(line)
                        if text:
                            yield text
        except httpx.TimeoutException as exc:
            raise GeminiConnectionError(f"Gemini stream timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise GeminiConnectionError(f"Could not reach Gemini: {exc}") from exc
