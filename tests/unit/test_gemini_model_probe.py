"""Checking that the configured Gemini models actually exist.

`DEFAULT_PRIMARY_MODEL` and `DEFAULT_FALLBACK_MODEL` are hardcoded ids, and the
comment beside them records that the previous 2.x ids "returned 404 for at least
one newly issued API key". A wrong id is not a loud failure: every AI call
returns an error that reads like a misconfigured key, so an operator looks at
their credentials rather than at the model name.

The probe asks the API which models it will serve, once, and says plainly if
the configured ones are not among them. Behind an environment flag because it
costs a network round trip at startup, and a deployment with no AI features
configured should not pay it.

Everything here is mocked. The ids themselves are NOT verified against the live
API by these tests -- doing that needs a real key, and is tracked separately as
an outstanding verification item.
"""

from __future__ import annotations

import httpx
import pytest

from archguard.llm import gemini


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch):
    for var in (
        "GEMINI_API_KEY",
        # resolve_api_key still honours this as a deprecated alias, so a probe
        # test that clears only GEMINI_API_KEY can still find a key.
        "OPENAI_API_KEY",
        "ARCHGUARD_PRIMARY_MODEL",
        "ARCHGUARD_FALLBACK_MODEL",
        "ARCHGUARD_VERIFY_LLM_ON_BOOT",
        "GEMINI_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)


def _models_response(ids: list[str]) -> httpx.Response:
    """What GET /models returns, in the OpenAI-compatible shape."""
    return httpx.Response(
        200,
        json={"object": "list", "data": [{"id": i, "object": "model"} for i in ids]},
    )


def _transport(handler):
    return httpx.MockTransport(handler)


# ------------------------------------------------------------- listing models


def test_lists_the_models_the_api_offers(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        return _models_response(["gemini-3.6-flash", "gemini-3.5-flash-lite"])

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    models = gemini.list_available_models(transport=_transport(handler))

    assert models == ["gemini-3.6-flash", "gemini-3.5-flash-lite"]
    assert seen["url"].endswith("/models"), seen["url"]
    assert seen["auth"] == "Bearer test-key", "the probe must authenticate"


def test_a_listing_failure_is_reported_not_raised(monkeypatch):
    """A probe that throws would take the whole application down at boot over
    a feature that is optional."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "upstream boom"}})

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert gemini.list_available_models(transport=_transport(handler)) is None


def test_a_network_error_is_reported_not_raised(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert gemini.list_available_models(transport=_transport(handler)) is None


# --------------------------------------------------------------- verification


def test_reports_both_models_present(monkeypatch):
    def handler(request):
        return _models_response(
            [gemini.primary_model(), gemini.fallback_model(), "something-else"]
        )

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    result = gemini.verify_configured_models(transport=_transport(handler))

    assert result.checked is True
    assert result.ok is True
    assert result.missing == []


def test_names_the_model_that_is_missing(monkeypatch):
    """The whole point: say which id is wrong, not merely that something is."""
    def handler(request):
        return _models_response([gemini.fallback_model()])

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    result = gemini.verify_configured_models(transport=_transport(handler))

    assert result.ok is False
    assert result.missing == [gemini.primary_model()]
    assert gemini.primary_model() in result.detail


def test_reports_both_missing(monkeypatch):
    def handler(request):
        return _models_response(["gemini-1.0-pro"])

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    result = gemini.verify_configured_models(transport=_transport(handler))

    assert result.ok is False
    assert set(result.missing) == {gemini.primary_model(), gemini.fallback_model()}


def test_an_unreachable_api_is_not_a_wrong_model(monkeypatch):
    """`checked` distinguishes "we asked and they are missing" from "we could
    not ask". Reporting the second as the first would send an operator hunting
    a model id when their network is the problem.
    """
    def handler(request):
        raise httpx.ConnectError("down")

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    result = gemini.verify_configured_models(transport=_transport(handler))

    assert result.checked is False
    assert result.ok is False
    assert result.missing == []


def test_no_key_means_no_probe(monkeypatch):
    """Nothing to verify, and no call to make."""
    called = False

    def handler(request):
        nonlocal called
        called = True
        return _models_response([])

    result = gemini.verify_configured_models(transport=_transport(handler))

    assert result.checked is False
    assert called is False, "the probe called the API without a key"


def test_an_overridden_model_id_is_what_gets_checked(monkeypatch):
    """Operators can point at a different model; the probe must follow them
    rather than check the defaults."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("ARCHGUARD_PRIMARY_MODEL", "my-custom-model")

    def handler(request):
        return _models_response(["my-custom-model", gemini.fallback_model()])

    result = gemini.verify_configured_models(transport=_transport(handler))
    assert result.ok is True


# ------------------------------------------------------------- startup probe


def test_the_probe_is_off_by_default(monkeypatch):
    """It costs a network round trip at boot; a deployment with no AI features
    configured should not pay for it."""
    assert gemini.should_verify_on_boot() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
def test_the_probe_can_be_turned_on(monkeypatch, value):
    monkeypatch.setenv("ARCHGUARD_VERIFY_LLM_ON_BOOT", value)
    assert gemini.should_verify_on_boot() is True


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_falsey_values_leave_it_off(monkeypatch, value):
    monkeypatch.setenv("ARCHGUARD_VERIFY_LLM_ON_BOOT", value)
    assert gemini.should_verify_on_boot() is False


# ----------------------------------------------------- reported to the caller


def test_ai_is_reported_unavailable_without_a_key(monkeypatch):
    """So the interface can say so up front instead of letting someone type a
    question, wait, and receive an error."""
    from archguard.dashboard import _capabilities

    status = _capabilities.ai_status()
    assert status["available"] is False
    assert "GEMINI_API_KEY" in status["reason"]


def test_ai_is_reported_available_with_a_key(monkeypatch):
    from archguard.dashboard import _capabilities

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    status = _capabilities.ai_status()
    assert status["available"] is True


def test_a_failed_boot_probe_marks_ai_unavailable(monkeypatch):
    """A key that is set but points at a model the API will not serve is not
    working AI, and saying "available" would be a lie the first click exposes.
    """
    from archguard.dashboard import _capabilities

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    _capabilities.record_model_check(
        gemini.ModelCheck(checked=True, ok=False, missing=["gemini-x"], detail="missing")
    )
    try:
        status = _capabilities.ai_status()
        assert status["available"] is False
        assert "gemini-x" in status["reason"]
    finally:
        _capabilities.record_model_check(None)


def test_an_unperformed_check_does_not_mark_ai_unavailable(monkeypatch):
    """The probe is optional. Not having run it says nothing about the models,
    and must not disable a working deployment.
    """
    from archguard.dashboard import _capabilities

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    _capabilities.record_model_check(
        gemini.ModelCheck(checked=False, ok=False, missing=[], detail="not checked")
    )
    try:
        assert _capabilities.ai_status()["available"] is True
    finally:
        _capabilities.record_model_check(None)


def test_mock_mode_reports_ai_available(monkeypatch):
    """ARCHGUARD_MOCK_LLM serves canned responses without an API call.

    The AI paths genuinely work in that mode -- it is how the browser tests
    drive the Advisor -- so reporting them unavailable would be false, and
    would disable the controls those tests use.
    """
    from archguard.dashboard import _capabilities

    monkeypatch.setenv("ARCHGUARD_MOCK_LLM", "1")
    status = _capabilities.ai_status()
    assert status["available"] is True
    assert status.get("mocked") is True


def test_mock_mode_off_falls_back_to_the_real_checks(monkeypatch):
    from archguard.dashboard import _capabilities

    monkeypatch.setenv("ARCHGUARD_MOCK_LLM", "0")
    assert _capabilities.ai_status()["available"] is False
