import time
import functools
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


# Removed: exponential_backoff was a duplicate of with_retry.
# Use with_retry() for all retry needs.
