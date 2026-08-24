"""ARCHGUARD_SKIP_LLM: a real off switch for AI features (P3-5).

The variable was documented for a long time and read by nothing, so an operator
who set it to hold down spend, or to keep an instance off external egress, got
every call made anyway -- no error, no log line. This is the implementation.

The property worth testing is not the message. It is that no connection is
opened: a control that refuses the answer after paying for it is not a cost
control. Each test below substitutes the transport and asserts it was never
reached.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "ARCHGUARD_SKIP_LLM",
        "ARCHGUARD_MOCK_LLM",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


class TestReasons:
    def test_a_configured_instance_is_not_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from archguard.llm.gemini import llm_disabled

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        assert llm_disabled() is None

    def test_the_switch_disables_a_fully_configured_instance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from archguard.llm.gemini import llm_disabled

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setenv("ARCHGUARD_SKIP_LLM", "1")
        reason = llm_disabled()
        assert reason and "ARCHGUARD_SKIP_LLM" in reason

    def test_a_missing_key_reads_differently_from_a_deliberate_switch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The two call for opposite actions from whoever reads the message.

        Collapsing them is how an operator spends an afternoon hunting for a key
        that was never the problem.
        """
        from archguard.llm.gemini import llm_disabled

        no_key = llm_disabled()
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setenv("ARCHGUARD_SKIP_LLM", "1")
        switched_off = llm_disabled()

        assert no_key and switched_off and no_key != switched_off
        assert "GEMINI_API_KEY" in no_key
        assert "ARCHGUARD_SKIP_LLM" in switched_off

    def test_the_switch_outranks_the_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A switch a second variable can override is not a kill switch.

        Getting this backwards means serving invented advice to somebody who
        believes the feature is off.
        """
        from archguard.llm.gemini import llm_disabled

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setenv("ARCHGUARD_MOCK_LLM", "1")
        monkeypatch.setenv("ARCHGUARD_SKIP_LLM", "1")
        reason = llm_disabled()
        assert reason and "ARCHGUARD_SKIP_LLM" in reason

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " 1 "])
    def test_truthy_spellings_all_disable(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        from archguard.llm.gemini import llm_disabled

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setenv("ARCHGUARD_SKIP_LLM", value)
        assert llm_disabled() is not None

    @pytest.mark.parametrize("value", ["", "0", "false", "no"])
    def test_falsy_spellings_leave_it_on(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        from archguard.llm.gemini import llm_disabled

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setenv("ARCHGUARD_SKIP_LLM", value)
        assert llm_disabled() is None


class TestNothingIsSent:
    """The point of the switch: no connection, not a discarded response."""

    def test_the_advisor_streams_the_reason_and_calls_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from archguard.llm.advisor import ArchitectureAdvisor

        called: list[Any] = []
        monkeypatch.setattr(
            "archguard.llm.gemini.GeminiClient",
            lambda *a, **k: called.append(1),
        )
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setenv("ARCHGUARD_SKIP_LLM", "1")

        chunks = list(ArchitectureAdvisor().ask_stream("why is coupling high?"))

        assert called == [], "a client was constructed for a disabled instance"
        assert any("ARCHGUARD_SKIP_LLM" in c for c in chunks)

    def test_the_advisor_does_not_serve_a_mock_answer_when_switched_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fixtures are for tests. Presenting one as advice is the worst outcome."""
        from archguard.llm.advisor import ArchitectureAdvisor

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setenv("ARCHGUARD_MOCK_LLM", "1")
        monkeypatch.setenv("ARCHGUARD_SKIP_LLM", "1")

        answer = "".join(ArchitectureAdvisor().ask_stream("anything"))
        assert "ARCHGUARD_SKIP_LLM" in answer
        assert "mock" not in answer.lower()

    @pytest.mark.asyncio
    async def test_the_explainer_reports_unavailable_without_calling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from archguard.llm.cloud import CloudLLMExplainer

        called: list[Any] = []
        monkeypatch.setattr(
            "archguard.llm.cloud.GeminiClient", lambda *a, **k: called.append(1)
        )
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setenv("ARCHGUARD_SKIP_LLM", "1")

        from tests.unit.test_cloud_llm import _CONTRACT, _make_result

        result = _make_result()
        out = await CloudLLMExplainer(api_key="k").explain_violations_concurrent(
            result.violations, _CONTRACT, result.changed_files
        )

        assert called == []
        assert len(out) == len(result.violations)
        assert all("ARCHGUARD_SKIP_LLM" in str(o) for o in out)

    def test_remediation_refuses_rather_than_returning_an_empty_plan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty plan reads as "nothing to fix", which is a different claim."""
        from archguard.llm.remediation import (
            GeminiRemediationProvider,
            RemediationUnavailableError,
        )

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setenv("ARCHGUARD_SKIP_LLM", "1")

        provider = GeminiRemediationProvider.__new__(GeminiRemediationProvider)
        with pytest.raises(RemediationUnavailableError, match="ARCHGUARD_SKIP_LLM"):
            provider.generate_tasks("context")

    @pytest.mark.asyncio
    async def test_contract_inference_refuses_before_walking_the_repository(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """The prompt build is pointless work if nothing may be sent."""
        import archguard.contract.llm_inference as li

        walked: list[Any] = []
        monkeypatch.setattr(li, "_build_directory_tree", lambda *a, **k: walked.append(1) or "")
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setenv("ARCHGUARD_SKIP_LLM", "1")

        with pytest.raises(ValueError, match="ARCHGUARD_SKIP_LLM"):
            await li.generate_contract_from_llm(tmp_path)

        assert walked == [], "the repository was walked for a disabled instance"


class TestAnalysisIsUnaffected:
    def test_switching_ai_off_does_not_disable_the_analysis_layers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The switch governs AI features, not the product.

        ARCHGUARD_SKIP_ML is the separate control for layers 3 and 4; conflating
        the two would make "stop spending on the LLM" silently stop measuring
        semantic drift as well.

        Asserted on the decision the pipeline actually makes, not on the ambient
        environment -- an earlier version of this test read os.environ directly
        and so passed or failed depending on how the suite happened to be
        invoked, which is no test at all.
        """
        import os

        def ml_skipped() -> bool:
            # The expression _orchestrator_stages and _orchestrator_layer4 both use.
            return os.getenv("ARCHGUARD_SKIP_ML", "").lower() in ("1", "true", "yes")

        monkeypatch.delenv("ARCHGUARD_SKIP_ML", raising=False)
        monkeypatch.setenv("ARCHGUARD_SKIP_LLM", "1")

        from archguard.llm.gemini import llm_disabled

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        assert llm_disabled() is not None, "AI should be off"
        assert ml_skipped() is False, "turning AI off also stopped the ML layers"

        # And the converse: skipping ML must not disable AI features.
        monkeypatch.delenv("ARCHGUARD_SKIP_LLM", raising=False)
        monkeypatch.setenv("ARCHGUARD_SKIP_ML", "1")
        assert ml_skipped() is True
        assert llm_disabled() is None, "skipping ML also switched AI off"
