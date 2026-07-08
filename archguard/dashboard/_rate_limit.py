import time
import threading
from collections import deque
from fastapi import Request, Response, HTTPException, status
from archguard.dashboard._auth import _real_client_ip

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

def rate_limiter(request: Request, response: Response = None) -> None:
    if response is None:
        response = Response()
    client_ip = _real_client_ip(request)
    now = time.time()

    with _RATE_LOCK:
        if client_ip not in RATE_LIMITS:
            RATE_LIMITS[client_ip] = deque()

        history = RATE_LIMITS[client_ip]

        while history and history[0] < now - RATE_LIMIT_WINDOW:
            history.popleft()

        remaining = max(0, RATE_LIMIT_MAX_REQUESTS - len(history) - 1)
        response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_MAX_REQUESTS)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(now + RATE_LIMIT_WINDOW))

        if len(history) >= RATE_LIMIT_MAX_REQUESTS:
            response.headers["Retry-After"] = str(int(RATE_LIMIT_WINDOW))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests",
            )

        history.append(now)

def _llm_rate_limit(request: Request, response: Response = None) -> None:
    if response is None:
        response = Response()
    client_ip = _real_client_ip(request)
    now = time.time()

    with _LLM_RATE_LOCK:
        if client_ip not in _LLM_LIMITS:
            _LLM_LIMITS[client_ip] = deque()

        history = _LLM_LIMITS[client_ip]

        while history and history[0] < now - RATE_LIMIT_WINDOW:
            history.popleft()

        remaining = max(0, _LLM_MAX - len(history) - 1)
        response.headers["X-RateLimit-Limit"] = str(_LLM_MAX)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(now + RATE_LIMIT_WINDOW))

        if len(history) >= _LLM_MAX:
            response.headers["Retry-After"] = str(int(RATE_LIMIT_WINDOW))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many LLM requests",
            )

        history.append(now)
