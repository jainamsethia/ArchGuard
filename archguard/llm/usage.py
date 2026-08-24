"""Token accounting for LLM calls.

The endpoint reports what every call cost and the client was throwing it away,
so an operator had no way to answer "how much is this spending" short of
reading the provider's billing page. The numbers are free -- they arrive in the
response body -- and this records them.

In Redis rather than in the process, for the same reason every other durable
counter here is: an in-process total resets on deploy and reports one replica's
share of the truth, which is worse than no number because it looks like one.

Recording is best-effort throughout. A metrics counter that can fail a paid API
call it was only observing would be a poor trade.
"""

from __future__ import annotations

import logging
from typing import cast

logger = logging.getLogger(__name__)

#: One key per figure, incremented forever. Counters, not gauges: Prometheus
#: works out rates, and a total that only goes up survives a scrape being
#: missed.
_PREFIX = "archguard:llm:usage"

FIELDS = ("calls", "prompt_tokens", "completion_tokens", "total_tokens")


def _key(field: str) -> str:
    return f"{_PREFIX}:{field}"


def record(prompt_tokens: int, completion_tokens: int, total_tokens: int) -> None:
    """Add one call's usage to the running totals. Never raises."""
    try:
        from archguard.redis_client import get_redis

        client = get_redis()
        if client is None:
            return
        pipe = client.pipeline()
        pipe.incrby(_key("calls"), 1)
        pipe.incrby(_key("prompt_tokens"), max(0, prompt_tokens))
        pipe.incrby(_key("completion_tokens"), max(0, completion_tokens))
        pipe.incrby(_key("total_tokens"), max(0, total_tokens))
        pipe.execute()
    except Exception as exc:
        # Debug, not warning: an instance without Redis is a development one,
        # and a line per LLM call about a counter nobody configured is noise
        # that trains people to ignore the log.
        logger.debug("Could not record LLM usage: %s", exc)


def totals() -> dict[str, int]:
    """The running totals, or zeros when they cannot be read.

    Zeros rather than an exception: the caller is /metrics, and a metrics
    endpoint that fails when one of its sources is unavailable goes quiet
    exactly when somebody is looking at it.
    """
    out = dict.fromkeys(FIELDS, 0)
    try:
        from archguard.redis_client import get_redis

        client = get_redis()
        if client is None:
            return out
        # Sync client; redis-py's shared base class is what makes the
        # annotation ``Awaitable[T] | T``. Same cast as _sessions.py.
        values = cast(
            "list[bytes | str | None]", client.mget([_key(f) for f in FIELDS])
        )
        for field, raw in zip(FIELDS, values, strict=True):
            if raw is not None:
                out[field] = int(raw)
    except Exception as exc:
        logger.debug("Could not read LLM usage totals: %s", exc)
    return out
