import os
import time
import threading
from typing import Any
from archguard.llm.advisor import ArchitectureAdvisor
from archguard.llm.openai_provider import OpenAIAdvisorProvider

_SESSION_LOCK = threading.Lock()
SESSION_STORE: dict[str, dict[str, Any]] = {}
SESSION_TTL_SECONDS = int(
    os.environ.get("ARCHGUARD_SESSION_TTL", "3600")
)  # 1 h default - controls AI Advisor conversation memory, NOT the auth cookie lifetime

def _purge_expired_sessions() -> None:
    """Remove sessions older than SESSION_TTL_SECONDS. Called opportunistically."""
    now = time.time()
    with _SESSION_LOCK:
        expired = [
            k for k, v in SESSION_STORE.items() if now - v["_ts"] > SESSION_TTL_SECONDS
        ]
        for k in expired:
            del SESSION_STORE[k]

def _build_advisor() -> ArchitectureAdvisor:
    """Construct an ArchitectureAdvisor using the configured provider.

    Uses OpenAIAdvisorProvider for the session-based analysis endpoint (initial
    recommendations). The streaming chat endpoint /api/v1/advisor/ask uses
    ArchitectureAdvisor.ask_stream() directly via the Anthropic SDK.
    """
    provider = OpenAIAdvisorProvider()
    return ArchitectureAdvisor(provider)
