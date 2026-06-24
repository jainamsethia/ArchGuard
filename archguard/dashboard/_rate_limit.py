import time
import threading
from collections import deque
from fastapi import Request, HTTPException, status

try:
    from cachetools import TTLCache as RateLimitCache
except ImportError:
    # Fallback for tests if cachetools is not installed
    class RateLimitCache(dict):  # type: ignore
        def __init__(self, maxsize, ttl):  # type: ignore[no-untyped-def]
            pass

RATE_LIMIT_WINDOW = 60.0
RATE_LIMIT_MAX_REQUESTS = 50

_RATE_LOCK = threading.Lock()
RATE_LIMITS: RateLimitCache[str, deque[float]] = RateLimitCache(
    maxsize=10_000, ttl=RATE_LIMIT_WINDOW * 2
)

_LLM_MAX = 10
_LLM_LIMITS: RateLimitCache[str, deque[float]] = RateLimitCache(
    maxsize=10_000, ttl=RATE_LIMIT_WINDOW * 2
)
_LLM_RATE_LOCK = threading.Lock()

def rate_limiter(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    with _RATE_LOCK:
        if client_ip not in RATE_LIMITS:
            RATE_LIMITS[client_ip] = deque()

        history = RATE_LIMITS[client_ip]

        while history and history[0] < now - RATE_LIMIT_WINDOW:
            history.popleft()

        if len(history) >= RATE_LIMIT_MAX_REQUESTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests",
            )

        history.append(now)

def _llm_rate_limit(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    with _LLM_RATE_LOCK:
        if client_ip not in _LLM_LIMITS:
            _LLM_LIMITS[client_ip] = deque()

        history = _LLM_LIMITS[client_ip]

        while history and history[0] < now - RATE_LIMIT_WINDOW:
            history.popleft()

        if len(history) >= _LLM_MAX:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many LLM requests",
            )

        history.append(now)
