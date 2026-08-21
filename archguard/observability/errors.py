"""Error reporting, if a DSN is configured.

``_global_exception_handler`` logs unhandled exceptions into a logger that was
never configured (C1, now fixed) -- so production 500s were invisible twice
over: once because nothing shipped them anywhere, and once because the log line
itself went nowhere. Logging is configured now; this is the other half.

Entirely optional. Sentry is not a dependency: the SDK is imported only when
``SENTRY_DSN`` is set, and a deployment that does not want it installs nothing
and sees a single informational log line at startup.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

#: Fraction of transactions traced. Errors are always reported; traces are the
#: expensive part, and 0 is the right default for a service whose slowest
#: operation is deliberately slow.
DEFAULT_TRACES_SAMPLE_RATE = 0.0


def _scrub(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any] | None:
    """Drop anything that should not leave the building.

    Sentry captures request data by default, and this application's requests
    carry a session cookie and, on the OAuth callback, an authorization code.
    Neither belongs in a third-party error tracker, and "we will configure the
    scrubbing later" is how they end up there.
    """
    request = event.get("request") or {}
    headers = request.get("headers") or {}
    for name in list(headers):
        if name.lower() in ("cookie", "authorization", "x-api-key"):
            headers[name] = "[redacted]"
    if "cookies" in request:
        request["cookies"] = "[redacted]"
    # The OAuth callback's query string contains a single-use code. Short-lived,
    # but a code sitting in an error report is a code someone can replay.
    if request.get("query_string"):
        query = str(request["query_string"])
        if "code=" in query or "token=" in query:
            request["query_string"] = "[redacted]"
    return event


def configure_error_reporting() -> None:
    """Wire up Sentry when SENTRY_DSN is set. Never raises.

    A failure to start error reporting must not stop the application: the
    service is the product, the telemetry is not.
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        logger.info(
            "SENTRY_DSN is not set; unhandled exceptions will be logged but "
            "not reported anywhere."
        )
        return

    try:
        import sentry_sdk
    except ImportError:
        logger.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed. Install it "
            "with `pip install sentry-sdk` or unset SENTRY_DSN."
        )
        return

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=os.environ.get("ENVIRONMENT", "development"),
            release=os.environ.get("ARCHGUARD_RELEASE") or None,
            traces_sample_rate=float(
                os.environ.get(
                    "SENTRY_TRACES_SAMPLE_RATE", str(DEFAULT_TRACES_SAMPLE_RATE)
                )
            ),
            # Off by default. The bodies here contain repository URLs and, on
            # the advisor endpoint, whatever the user typed.
            send_default_pii=False,
            before_send=_scrub,
        )
        logger.info("Error reporting enabled (Sentry)")
    except Exception:
        logger.exception("Could not initialise error reporting; continuing without it")
