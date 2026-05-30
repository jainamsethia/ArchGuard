"""Safe async-to-sync execution utilities."""
from __future__ import annotations
import asyncio
from typing import Any, Coroutine, TypeVar
T = TypeVar("T")
def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine, handling both the no-loop and already-running-loop cases.
    - If no event loop is running: uses asyncio.run() (clean shutdown).
    - If a loop is already running: creates a new thread with its own event loop.
    This is safe for CLI usage where the running loop is the test harness.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — safe to use asyncio.run()
        return asyncio.run(coro)
    # A loop is already running (e.g., pytest-asyncio, Jupyter).
    # Run in a separate thread to avoid nesting.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_in_new_loop, coro)
        import typing
        return typing.cast(T, future.result())

def _run_in_new_loop(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run coroutine in a brand-new event loop on the calling thread."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
