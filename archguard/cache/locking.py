"""File-based locking with timeout for ArchGuard.

Uses ``msvcrt`` on Windows and ``fcntl`` on Unix/macOS.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

LOCK_TIMEOUT = float(os.getenv("ARCHGUARD_LOCK_TIMEOUT", "30.0"))
LOCK_RETRY_INTERVAL = 0.05


@contextmanager
def file_lock(lock_path: Path | str, timeout: float = LOCK_TIMEOUT) -> Generator[None, None, None]:
    """
    OS-level file lock. Automatically released on process death.
    Uses fcntl with a spin loop on Unix, msvcrt on Windows.
    Thread-safe (unlike SIGALRM). Works in containerized runtimes.
    """
    lock_path = Path(lock_path)
    lock_path.touch(exist_ok=True)
    with open(lock_path, "w") as lock_file:
        if sys.platform == "win32":
            # Windows: msvcrt exclusive lock
            import time
            import msvcrt
            deadline = time.monotonic() + timeout
            while True:
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            f"Could not acquire file lock on {lock_path} within {timeout}s. "
                            "Another archguard process may be running."
                        )
                    time.sleep(LOCK_RETRY_INTERVAL)
            try:
                yield
            finally:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            # Unix: fcntl non-blocking lock with spin loop
            import time
            import fcntl
            deadline = time.monotonic() + timeout
            acquired = False
            while time.monotonic() < deadline:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    time.sleep(LOCK_RETRY_INTERVAL)
            if not acquired:
                raise TimeoutError(
                    f"Could not acquire file lock on {lock_path} within {timeout}s. "
                    "Another archguard process may be running."
                )
            try:
                yield
            finally:
                if acquired:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

