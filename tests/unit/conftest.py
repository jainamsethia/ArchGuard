"""Unit-test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def reset_rate_limit_state() -> None:
    """Clear the in-process rate-limiter buckets before each test.

    The dashboard rate limiter keys requests by client IP in module-level
    caches; without a reset, requests made by earlier test files count against
    later ones and auth/429 assertions become order-dependent.
    """
    try:
        from archguard.dashboard import _rate_limit
    except ImportError:  # dashboard extra not installed
        return
    _rate_limit.reset_rate_limits()
