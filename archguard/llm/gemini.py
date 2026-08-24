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
from collections.abc import AsyncIterator, Iterator
from typing import Any

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


class GeminiModelNotFoundError(GeminiError):
    """The API has no such model.

    Its own tier, because it is neither transient nor terminal. Retrying the
    same model id is pointless -- it will not appear -- but the *other* tier may
    be perfectly healthy, so a caller holding a primary/fallback pair should
    move to the next one rather than give up.

    This is not hypothetical. The 2.x ids this client shipped with are still
    listed in Google's documentation yet return 404 for newly issued keys, and
    Google retires model ids on a published schedule. Folding that into the
    generic 4xx bucket meant a retired primary took the fallback tier down with
    it -- the one failure the two-tier design exists to survive.
    """


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
    GeminiModelNotFoundError,
    GeminiResponseError,
    ValueError,
    TypeError,
)

#: Not worth retrying on the *same* model, but worth trying the next tier.
#:
#: Listed in NON_RETRYABLE_ERRORS above so `with_retry` does not spend three
#: attempts asking for a model that does not exist. Callers that hold a
#: primary/fallback pair must catch this *before* the non-retryable tuple and
#: advance to the next model -- see CloudLLMExplainer.explain_violations_concurrent.
TRY_NEXT_MODEL_ERRORS: tuple[type[Exception], ...] = (GeminiModelNotFoundError,)


def llm_disabled(explicit_key: str | None = None) -> str | None:
    """Why AI features are switched off, or ``None`` when they may run.

    One place to ask, so a new call site cannot forget the kill switch and
    quietly start spending. Every caller checks this *before* opening a
    connection -- a control that refuses the response after paying for it is
    not a cost control.

    The two reasons are kept distinct on purpose. "No key" and "deliberately
    disabled" call for opposite actions from whoever reads the message, and
    collapsing them into one string is how an operator spends an afternoon
    looking for a key that was never the problem.

    ``ARCHGUARD_SKIP_LLM`` outranks ``ARCHGUARD_MOCK_LLM``. A switch that a
    second variable can override is not a kill switch, and the failure mode of
    getting this backwards is serving invented advice to someone who thinks the
    feature is off.
    """
    if os.environ.get("ARCHGUARD_SKIP_LLM", "").strip().lower() in ("1", "true", "yes"):
        return (
            "AI features are switched off on this instance "
            "(ARCHGUARD_SKIP_LLM). Analysis itself is unaffected."
        )
    if os.environ.get("ARCHGUARD_MOCK_LLM") == "1":
        return None
    if not resolve_api_key(explicit_key):
        return (
            "Gemini API key not configured. "
            "Set GEMINI_API_KEY to enable AI features."
        )
    return None


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
    except Exception:
        detail = ""

    if status in (401, 403):
        raise GeminiAuthError(f"Gemini rejected the credentials ({status}): {detail}")
    if status == 429:
        retry_after = response.headers.get("Retry-After", "")
        suffix = f" Retry after {retry_after}s." if retry_after else ""
        raise GeminiRateLimitError(f"Gemini rate limit exceeded.{suffix} {detail}")
    if status == 404:
        raise GeminiModelNotFoundError(
            f"Gemini has no such model ({status}): {detail}"
        )
    if status >= 500:
        raise GeminiServerError(f"Gemini server error ({status}): {detail}")
    raise GeminiResponseError(f"Gemini request failed ({status}): {detail}")


def _usage_of(payload: dict[str, Any]) -> tuple[int, int, int] | None:
    """(prompt, completion, total) tokens from a response, if it reported any.

    Returns ``None`` rather than zeros when absent: a call whose usage was not
    reported is not a call that cost nothing, and recording it as zero would
    quietly understate the bill.
    """
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    try:
        return (
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            int(usage.get("total_tokens") or 0),
        )
    except (TypeError, ValueError):
        return None


def _record_usage(payload: dict[str, Any]) -> None:
    """Add a response's reported usage to the running totals. Never raises."""
    reported = _usage_of(payload)
    if reported is None:
        return
    from archguard.llm import usage

    usage.record(*reported)


def _stream_usage(line: str) -> tuple[int, int, int] | None:
    """Usage carried by one streamed chunk, if it carries any.

    Returns rather than records, because a stream emits *more than one*
    usage-bearing chunk -- measured against the live endpoint: two for a
    one-word answer. Recording each of them counted a single call twice and
    summed figures that overlap, which inflates a spend metric into something
    nobody can act on. The caller keeps the last one and records it once.
    """
    if not line.startswith("data:"):
        return None
    body = line[5:].strip()
    if not body or body == "[DONE]":
        return None
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    return _usage_of(payload)


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
            # Without this a streamed answer reports no usage at all, and the
            # Advisor -- the chattiest feature -- would be missing from the
            # spend figure entirely. Verified against the endpoint: the final
            # chunks carry a usage object when it is asked for.
            payload["stream_options"] = {"include_usage": True}
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
        _record_usage(body)
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
            with httpx.Client(timeout=self.timeout) as client, client.stream(
                "POST", self._endpoint, json=payload, headers=self._headers()
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    _raise_for_status(response)
                last_usage: tuple[int, int, int] | None = None
                for line in response.iter_lines():
                    reported = _stream_usage(line)
                    if reported is not None:
                        # Kept, not recorded: see _stream_usage. The final one
                        # is the complete figure for the call.
                        last_usage = reported
                    text = _delta_text(line)
                    if text:
                        yield text
                if last_usage is not None:
                    from archguard.llm import usage as _usage

                    _usage.record(*last_usage)
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
        _record_usage(body)
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
            async with httpx.AsyncClient(timeout=self.timeout) as client, client.stream(
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
