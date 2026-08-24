"""Two-tier model handling in contract generation.

``generate_contract_from_llm`` tries a primary model then a cheaper fallback.
Which failures should move to the second tier is the whole question, and this
file pins all four answers:

  * transient (rate limit, 5xx, network)  -> try the fallback
  * the model id does not exist (404)     -> try the fallback
  * bad credentials / malformed request   -> do not; both tiers fail the same
    way, and burning an attempt on the second one reports *its* failure and
    hides the real cause
  * anything unclassified                 -> propagate, rather than be reported
    as "failed on both models", which turns a bug into a vague LLM outage

The loop used to catch bare ``Exception``, which collapsed all four into the
second-tier path. ``archguard/llm/cloud.py`` had already been fixed for exactly
this; its copy of the loop here had not.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

import archguard.contract.llm_inference as li
from archguard.llm.cloud import FALLBACK_MODEL, PRIMARY_MODEL, CloudLLMExplainer
from archguard.llm.gemini import (
    GeminiAuthError,
    GeminiModelNotFoundError,
    GeminiRateLimitError,
)


def _run(side_effect):
    """Drive generate_contract_from_llm with *side_effect* standing in for the API."""
    return patch.object(
        CloudLLMExplainer, "_call_api", autospec=True, side_effect=side_effect
    )


def _no_filesystem():
    """The prompt inputs are not under test here."""
    return (
        patch.dict("os.environ", {"GEMINI_API_KEY": "gemini-fake-key"}),
        patch("archguard.contract.llm_inference._build_directory_tree", return_value=""),
        patch("archguard.contract.llm_inference._extract_module_docstrings", return_value=""),
        patch("archguard.contract.llm_inference._read_readme_excerpt", return_value=""),
        patch("archguard.contract.validator.validate_contract"),
    )


@pytest.mark.asyncio
async def test_generate_contract_from_llm_fallback():
    """A transient primary failure falls back to the cheaper model.

    Previously raised a bare RuntimeError, which only reached the fallback
    because the loop caught bare Exception. A rate limit is what this is
    standing in for, so it now raises one -- the assertion is unchanged.
    """
    call_log = []

    def side_effect(self, prompt, model, system=""):
        call_log.append(model)
        if model == PRIMARY_MODEL:
            raise GeminiRateLimitError("rate limited")
        return '{"modules": []}', "stop"

    a, b, c, d, e = _no_filesystem()
    with _run(side_effect), a, b, c, d, e:
        result = await li.generate_contract_from_llm(Path("."))

    assert call_log == [PRIMARY_MODEL, FALLBACK_MODEL]
    assert result == {"modules": []}


@pytest.mark.asyncio
async def test_a_retired_model_id_falls_back():
    """Google retires model ids; a dead primary must not take the pair down."""
    call_log = []

    def side_effect(self, prompt, model, system=""):
        call_log.append(model)
        if model == PRIMARY_MODEL:
            raise GeminiModelNotFoundError("models/gemini-x is not found")
        return '{"modules": []}', "stop"

    a, b, c, d, e = _no_filesystem()
    with _run(side_effect), a, b, c, d, e:
        result = await li.generate_contract_from_llm(Path("."))

    assert call_log == [PRIMARY_MODEL, FALLBACK_MODEL]
    assert result == {"modules": []}


@pytest.mark.asyncio
async def test_bad_credentials_do_not_burn_the_fallback_tier():
    """The same key fails on both tiers, so trying twice only hides the cause."""
    call_log = []

    def side_effect(self, prompt, model, system=""):
        call_log.append(model)
        raise GeminiAuthError("invalid key")

    a, b, c, d, e = _no_filesystem()
    with _run(side_effect), a, b, c, d, e, pytest.raises(GeminiAuthError):
        await li.generate_contract_from_llm(Path("."))

    assert call_log == [PRIMARY_MODEL], (
        "the auth failure was retried on the fallback model, so the error the "
        "caller sees is the second one and the real cause is lost"
    )


@pytest.mark.asyncio
async def test_an_unclassified_error_propagates_instead_of_being_relabelled():
    """An unexpected exception type is a bug, not an LLM outage.

    Every HTTP and network condition is mapped onto the typed hierarchy by the
    Gemini client, so anything reaching here uncategorised came from our own
    code. Reporting it as "contract generation failed on both models" would
    describe a working API as a broken one.
    """
    call_log = []

    def side_effect(self, prompt, model, system=""):
        call_log.append(model)
        raise KeyError("prompt template placeholder missing")

    a, b, c, d, e = _no_filesystem()
    with _run(side_effect), a, b, c, d, e, pytest.raises(KeyError):
        await li.generate_contract_from_llm(Path("."))

    assert call_log == [PRIMARY_MODEL]
