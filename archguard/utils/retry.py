import time
import functools
import random
from typing import TypeVar, Callable, Type, Any
import logging

logger = logging.getLogger(__name__)
T = TypeVar("T")


def with_retry(
    max_attempts: int = 3,
    backoff_base: float = 1.0,
    backoff_max: float = 30.0,
    retryable_exceptions: tuple[Type[Exception], ...] = (Exception,),
    non_retryable_exceptions: tuple[Type[Exception], ...] = (),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Exponential backoff decorator with jitter."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except non_retryable_exceptions:
                    raise
                except retryable_exceptions as e:
                    if attempt == max_attempts - 1:
                        raise

                    wait = min(backoff_base * (2**attempt), backoff_max)

                    # specific HTTP 429 check for Anthropic / requests
                    try:
                        if hasattr(e, "response") and e.response is not None:
                            status_code = getattr(e.response, "status_code", None)
                            if status_code == 429:
                                headers = getattr(e.response, "headers", {})
                                retry_after = headers.get("Retry-After") or headers.get(
                                    "retry-after"
                                )
                                if retry_after:
                                    wait = float(retry_after)
                    except Exception as parse_err:
                        logger.warning(
                            f"Non-critical failure in retry header parsing: {parse_err}"
                        )

                    logger.warning(
                        f"Attempt {attempt + 1} failed: {e}. Retrying in {wait:.1f}s..."
                    )
                    time.sleep(wait)
            raise RuntimeError("Maximum retries exceeded")

        return wrapper

    return decorator


def exponential_backoff(
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    retryable_exceptions: tuple[Type[Exception], ...] = (Exception,),
    retryable_status_codes: tuple[int, ...] = (429, 500, 502, 503, 504),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator for exponential backoff retry logic."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    if type(e).__name__ == "RateLimitExceededException":
                        reset_timestamp = getattr(e, "headers", {}).get(
                            "X-RateLimit-Reset", 0
                        )
                        if not reset_timestamp and hasattr(e, "response"):
                            reset_timestamp = getattr(e.response, "headers", {}).get(
                                "X-RateLimit-Reset", 0
                            )
                        wait = max(0, int(reset_timestamp) - int(time.time())) + 5
                        logger.warning(
                            "GitHub rate limit exceeded. Reset in %ds.", wait
                        )
                        if wait < 300:
                            time.sleep(wait)
                            continue
                        else:
                            from archguard.utils.errors import ArchGuardError

                            raise ArchGuardError(
                                f"GitHub API rate limit exceeded. Reset at {reset_timestamp}. "
                                "Consider reducing analysis frequency or using a GitHub App token."
                            ) from e
                    # Check if this is a rate limit or transient error
                    status = getattr(e, "status", None) or getattr(
                        e, "response", {}
                    ).get("status")
                    if status and status not in retryable_status_codes:
                        raise  # Non-retryable error, re-raise immediately

                    if attempt == max_retries:
                        logger.error(
                            "Max retries (%d) exceeded for %s",
                            max_retries,
                            func.__name__,
                        )
                        raise

                    delay = min(base_delay * (2**attempt), max_delay)
                    if jitter:
                        delay = delay * (0.5 + random.random() * 0.5)

                    logger.warning(
                        "Attempt %d/%d failed for %s (status=%s). Retrying in %.1fs",
                        attempt + 1,
                        max_retries,
                        func.__name__,
                        status,
                        delay,
                    )
                    time.sleep(delay)
            if last_exception is not None:
                raise last_exception
            raise RuntimeError("Max retries exceeded")

        return wrapper

    return decorator
