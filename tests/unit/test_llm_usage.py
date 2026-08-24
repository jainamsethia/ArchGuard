"""Token accounting for LLM calls (P3-5).

The endpoint reports what every call cost and the client threw it away, so the
only way to answer "how much is this spending" was the provider's billing page.

Two properties carry the weight. Recording must never be able to break the call
it is observing -- a metrics counter that can fail a paid API request is a poor
trade. And a call whose usage was not reported must not be recorded as zero: a
missing figure is not a free call, and writing it down as one quietly understates
the bill.
"""

from __future__ import annotations

from typing import Any

import pytest

from archguard.llm.gemini import _usage_of


class TestExtraction:
    def test_reads_the_three_figures(self) -> None:
        got = _usage_of(
            {"usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}}
        )
        assert got == (120, 30, 150)

    def test_a_response_without_usage_is_none_not_zero(self) -> None:
        """A call whose cost was not reported is not a call that cost nothing."""
        assert _usage_of({"choices": []}) is None
        assert _usage_of({"usage": None}) is None
        assert _usage_of({}) is None

    def test_a_malformed_usage_block_is_none(self) -> None:
        assert _usage_of({"usage": "lots"}) is None
        assert _usage_of({"usage": {"prompt_tokens": "many"}}) is None

    def test_missing_fields_default_to_zero_within_a_reported_block(self) -> None:
        # The block exists, so the call *was* accounted for; absent members are
        # genuinely zero rather than unknown.
        assert _usage_of({"usage": {"total_tokens": 9}}) == (0, 0, 9)


class TestRecordingIsBestEffort:
    def test_recording_never_raises_when_redis_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from archguard.llm import usage

        monkeypatch.setattr("archguard.redis_client.get_redis", lambda: None)
        usage.record(10, 5, 15)  # must not raise

    def test_recording_never_raises_when_redis_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The call has already been paid for; losing the counter is the cheap loss."""
        from archguard.llm import usage

        class Broken:
            def pipeline(self) -> Any:
                raise RuntimeError("redis is down")

        monkeypatch.setattr("archguard.redis_client.get_redis", Broken)
        usage.record(10, 5, 15)

    def test_totals_are_zeros_rather_than_an_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """/metrics must not go quiet exactly when somebody is looking at it."""
        from archguard.llm import usage

        monkeypatch.setattr("archguard.redis_client.get_redis", lambda: None)
        assert usage.totals() == dict.fromkeys(usage.FIELDS, 0)


class TestCountersAccumulate:
    def test_a_call_increments_every_counter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from archguard.llm import usage

        store: dict[str, int] = {}

        class Pipe:
            def incrby(self, key: str, amount: int) -> None:
                store[key] = store.get(key, 0) + amount

            def execute(self) -> None:
                pass

        class Fake:
            def pipeline(self) -> Pipe:
                return Pipe()

            def mget(self, keys: list[str]) -> list[Any]:
                return [store.get(k) for k in keys]

        monkeypatch.setattr("archguard.redis_client.get_redis", Fake)

        usage.record(100, 20, 120)
        usage.record(50, 10, 60)

        totals = usage.totals()
        assert totals["calls"] == 2
        assert totals["prompt_tokens"] == 150
        assert totals["completion_tokens"] == 30
        assert totals["total_tokens"] == 180

    def test_negative_figures_cannot_drive_a_total_backwards(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A counter that can decrease is not a counter."""
        from archguard.llm import usage

        store: dict[str, int] = {}

        class Pipe:
            def incrby(self, key: str, amount: int) -> None:
                assert amount >= 0, f"{key} was decremented by {amount}"
                store[key] = store.get(key, 0) + amount

            def execute(self) -> None:
                pass

        class Fake:
            def pipeline(self) -> Pipe:
                return Pipe()

        monkeypatch.setattr("archguard.redis_client.get_redis", Fake)
        usage.record(-5, -1, -6)


class TestStreamedCallsAreCounted:
    def test_the_streaming_request_asks_for_usage(self) -> None:
        """Without stream_options a streamed answer reports nothing.

        The Advisor is the chattiest feature and the only streaming one, so
        omitting this would leave the largest spender out of the figure while
        the metric still looked complete.
        """
        from archguard.llm.gemini import GeminiClient

        client = GeminiClient(api_key="k")
        payload = client._payload(
            [{"role": "user", "content": "hi"}],
            model=None,
            max_tokens=16,
            temperature=0.2,
            stream=True,
        )
        assert payload.get("stream_options") == {"include_usage": True}

    def test_a_one_shot_request_does_not_ask_for_it(self) -> None:
        from archguard.llm.gemini import GeminiClient

        client = GeminiClient(api_key="k")
        payload = client._payload(
            [{"role": "user", "content": "hi"}],
            model=None,
            max_tokens=16,
            temperature=0.2,
            stream=False,
        )
        assert "stream_options" not in payload

    def test_chunks_carrying_no_usage_report_none(self) -> None:
        from archguard.llm.gemini import _stream_usage

        # The shapes a stream actually produces.
        assert _stream_usage(": keep-alive") is None
        assert _stream_usage('data: {"choices":[{"delta":{"content":"hi"}}]}') is None
        assert _stream_usage("data: [DONE]") is None
        assert _stream_usage("data: not json") is None

    def test_a_usage_chunk_is_returned_not_recorded(self) -> None:
        """Returned so the caller can keep only the last one -- see below."""
        from archguard.llm.gemini import _stream_usage

        assert _stream_usage(
            'data: {"choices":[],"usage":{"prompt_tokens":8,'
            '"completion_tokens":1,"total_tokens":9}}'
        ) == (8, 1, 9)

    def test_a_stream_with_several_usage_chunks_counts_one_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Measured against the live endpoint: a one-word answer emits two.

        Recording each of them counted a single call twice and summed figures
        that overlap -- the first version of this did exactly that, and a real
        call came back as calls=2 with a total that did not match its own parts.
        A spend metric that overstates is one nobody can act on.
        """
        import httpx

        from archguard.llm.gemini import GeminiClient

        recorded: list[tuple[int, int, int]] = []
        monkeypatch.setattr(
            "archguard.llm.usage.record", lambda p, c, t: recorded.append((p, c, t))
        )

        chunks = [
            'data: {"choices":[{"delta":{"content":"ok"}}]}',
            'data: {"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":1,"total_tokens":140}}',
            'data: {"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":1,"total_tokens":9}}',
            "data: [DONE]",
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="\n".join(chunks))

        transport = httpx.MockTransport(handler)
        real_client = httpx.Client

        def fake_client(*a: Any, **k: Any) -> httpx.Client:
            return real_client(transport=transport, **k)

        monkeypatch.setattr(httpx, "Client", fake_client)

        list(GeminiClient(api_key="k").stream("hi"))

        assert len(recorded) == 1, f"one call recorded as {len(recorded)}"
        # The last figure, which is the complete one for the call.
        assert recorded[0] == (8, 1, 9)
