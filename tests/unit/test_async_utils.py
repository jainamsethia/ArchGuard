import asyncio
from archguard.utils.async_utils import run_async


async def _sample() -> int:
    return 42


def test_run_async_no_loop():
    assert run_async(_sample()) == 42


def test_run_async_in_running_loop():
    # Simulate calling from within a running loop (pytest-asyncio style)
    async def outer():
        return run_async(_sample())

    assert asyncio.run(outer()) == 42
