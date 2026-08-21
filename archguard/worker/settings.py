"""Queue connection settings.

Separate from ``main`` so the enqueue side can import it without importing the
worker entry point. ``WorkerSettings`` evaluates its connection settings in the
class body -- arq reads ``redis_settings`` as an attribute, not as a callable --
so importing ``main`` requires REDIS_URL, and the web process must not.
"""

from __future__ import annotations

import os
from typing import Any


def redis_settings() -> Any:
    """arq's connection settings, from the same REDIS_URL everything else uses."""
    from arq.connections import RedisSettings

    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        raise RuntimeError(
            "REDIS_URL is not set. The queue lives in Redis; there is nothing "
            "to enqueue to, and nothing for a worker to consume."
        )
    return RedisSettings.from_dsn(url)
