"""Tests for the Gemini client, ArchGuard's sole LLM provider.

The pieces worth pinning are the ones a migration gets wrong quietly: which
environment variable supplies the key, how HTTP status maps onto retryable vs
terminal errors, and whether streaming actually decodes server-sent events
rather than returning one blob at the end.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from archguard.llm.gemini import (
    DEFAULT_BASE_URL,
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_MODEL,
    DEFAULT_PRIMARY_MODEL,
    GeminiAuthError,
    GeminiClient,
    GeminiConnectionError,
    GeminiRateLimitError,
    GeminiResponseError,
    GeminiServerError,
    NON_RETRYABLE_ERRORS,
    RETRYABLE_ERRORS,
    _delta_text,
    fallback_model,
    primary_model,
    resolve_api_key,
    resolve_base_url,
    resolve_model,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("GEMINI_API_KEY", "OPENAI_API_KEY", "GEMINI_MODEL",
                "GEMINI_BASE_URL", "ARCHGUARD_PRIMARY_MODEL",
                "ARCHGUARD_FALLBACK_MODEL"):
        monkeypatch.delenv(var, raising=False)


def _response(status=200, payload=None, text="", headers=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload if payload is not None else {}
    r.text = text
    r.headers = headers or {}
    return r


def _ok(content="hello", finish="stop"):
    return _response(
        payload={"choices": [{"message": {"content": content}, "finish_reason": finish}]}
    )


# ---------------------------------------------------------------------------
# Configuration resolution
# ---------------------------------------------------------------------------


def test_gemini_key_is_preferred(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-key")
    assert resolve_api_key() == "gemini-key"


def test_openai_key_is_accepted_as_a_deprecated_alias(monkeypatch, caplog):
    """Existing deployments must not break silently on the rename."""
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-key")
    with caplog.at_level("WARNING"):
        assert resolve_api_key() == "legacy-key"
    assert "deprecated" in caplog.text.lower()
    assert "GEMINI_API_KEY" in caplog.text


def test_explicit_key_wins_over_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    assert resolve_api_key("explicit") == "explicit"


def test_missing_key_resolves_to_empty_not_an_error():
    assert resolve_api_key() == ""


def test_defaults(monkeypatch):
    assert resolve_model() == DEFAULT_MODEL
    assert resolve_base_url() == DEFAULT_BASE_URL
    assert primary_model() == DEFAULT_PRIMARY_MODEL
    assert fallback_model() == DEFAULT_FALLBACK_MODEL

    monkeypatch.setenv("GEMINI_MODEL", "custom-model")
    monkeypatch.setenv("ARCHGUARD_PRIMARY_MODEL", "custom-primary")
    monkeypatch.setenv("ARCHGUARD_FALLBACK_MODEL", "custom-fallback")
    assert resolve_model() == "custom-model"
    assert primary_model() == "custom-primary"
    assert fallback_model() == "custom-fallback"


def test_base_url_trailing_slash_is_normalised(monkeypatch):
    monkeypatch.setenv("GEMINI_BASE_URL", "https://example.test/v1/")
    assert resolve_base_url() == "https://example.test/v1"


def test_primary_and_fallback_are_different_tiers():
    """The resilience pattern is pointless if both tiers are the same model."""
    assert DEFAULT_PRIMARY_MODEL != DEFAULT_FALLBACK_MODEL


# ---------------------------------------------------------------------------
# Error mapping -- what may be retried, and what never should be
# ---------------------------------------------------------------------------


def test_missing_key_raises_auth_error_naming_the_variable():
    client = GeminiClient()
    with pytest.raises(GeminiAuthError, match="GEMINI_API_KEY"):
        client.complete("prompt")


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, GeminiAuthError),
        (403, GeminiAuthError),
        (429, GeminiRateLimitError),
        (500, GeminiServerError),
        (503, GeminiServerError),
        (400, GeminiResponseError),
    ],
)
def test_http_status_maps_to_typed_errors(status, expected):
    client = GeminiClient(api_key="k")
    with patch("httpx.Client.post", return_value=_response(status=status, text="err")):
        with pytest.raises(expected):
            client.complete("prompt")


def test_retry_classification_is_coherent():
    """Auth failures must never be classified as worth retrying."""
    assert GeminiRateLimitError in RETRYABLE_ERRORS
    assert GeminiServerError in RETRYABLE_ERRORS
    assert GeminiConnectionError in RETRYABLE_ERRORS
    assert GeminiAuthError in NON_RETRYABLE_ERRORS
    assert GeminiAuthError not in RETRYABLE_ERRORS


def test_timeout_and_network_errors_are_connection_errors():
    client = GeminiClient(api_key="k")
    with patch("httpx.Client.post", side_effect=httpx.TimeoutException("t")):
        with pytest.raises(GeminiConnectionError):
            client.complete("prompt")
    with patch("httpx.Client.post", side_effect=httpx.RequestError("boom")):
        with pytest.raises(GeminiConnectionError):
            client.complete("prompt")


def test_malformed_success_body_is_a_response_error():
    client = GeminiClient(api_key="k")
    with patch("httpx.Client.post", return_value=_response(payload={"nope": 1})):
        with pytest.raises(GeminiResponseError):
            client.complete("prompt")


# ---------------------------------------------------------------------------
# Requests and responses
# ---------------------------------------------------------------------------


def test_complete_returns_text_and_finish_reason():
    client = GeminiClient(api_key="k")
    with patch("httpx.Client.post", return_value=_ok("the answer", "length")):
        text, finish = client.complete("prompt")
    assert (text, finish) == ("the answer", "length")


def test_request_targets_the_openai_compatible_endpoint():
    client = GeminiClient(api_key="secret-key", model="m")
    with patch("httpx.Client.post", return_value=_ok()) as post:
        client.complete("prompt", system="sys")

    url = post.call_args[0][0]
    assert url == f"{DEFAULT_BASE_URL}/chat/completions"
    assert post.call_args[1]["headers"]["Authorization"] == "Bearer secret-key"
    payload = post.call_args[1]["json"]
    assert payload["model"] == "m"
    assert payload["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "prompt"},
    ]
    assert "stream" not in payload


def test_system_message_is_omitted_when_empty():
    client = GeminiClient(api_key="k")
    with patch("httpx.Client.post", return_value=_ok()) as post:
        client.complete("prompt")
    assert post.call_args[1]["json"]["messages"] == [
        {"role": "user", "content": "prompt"}
    ]


def test_model_override_beats_the_instance_default():
    client = GeminiClient(api_key="k", model="instance-model")
    with patch("httpx.Client.post", return_value=_ok()) as post:
        client.complete("prompt", model="call-model")
    assert post.call_args[1]["json"]["model"] == "call-model"


# ---------------------------------------------------------------------------
# Streaming -- SSE decoding
# ---------------------------------------------------------------------------


def test_delta_text_decodes_only_content_carrying_lines():
    assert _delta_text('data: {"choices":[{"delta":{"content":"hi"}}]}') == "hi"
    assert _delta_text("data: [DONE]") is None
    assert _delta_text("") is None
    assert _delta_text(": keep-alive comment") is None
    assert _delta_text("data: not json") is None
    # A role-only opening delta carries no text.
    assert _delta_text('data: {"choices":[{"delta":{"role":"assistant"}}]}') is None


def test_stream_yields_fragments_in_order():
    lines = [
        'data: {"choices":[{"delta":{"role":"assistant"}}]}',
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        "",
        'data: {"choices":[{"delta":{"content":", world"}}]}',
        "data: [DONE]",
    ]
    resp = MagicMock()
    resp.status_code = 200
    resp.iter_lines.return_value = iter(lines)
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=resp)
    ctx.__exit__ = MagicMock(return_value=False)

    client = GeminiClient(api_key="k")
    with patch("httpx.Client.stream", return_value=ctx):
        assert list(client.stream("prompt")) == ["Hello", ", world"]


def test_stream_sets_the_stream_flag_on_the_request():
    resp = MagicMock()
    resp.status_code = 200
    resp.iter_lines.return_value = iter(["data: [DONE]"])
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=resp)
    ctx.__exit__ = MagicMock(return_value=False)

    client = GeminiClient(api_key="k")
    with patch("httpx.Client.stream", return_value=ctx) as stream:
        list(client.stream("prompt"))
    assert stream.call_args[1]["json"]["stream"] is True


def test_stream_maps_error_status_before_yielding():
    resp = MagicMock()
    resp.status_code = 429
    resp.headers = {}
    resp.text = "slow down"
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=resp)
    ctx.__exit__ = MagicMock(return_value=False)

    client = GeminiClient(api_key="k")
    with patch("httpx.Client.stream", return_value=ctx):
        with pytest.raises(GeminiRateLimitError):
            list(client.stream("prompt"))
