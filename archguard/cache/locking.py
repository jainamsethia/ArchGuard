"""File-based locking with timeout for ArchGuard.

Uses ``msvcrt`` on Windows and ``fcntl`` on Unix/macOS.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

LOCK_TIMEOUT = 30  # seconds

@contextmanager
def file_lock(lock_path: Path, timeout: float = LOCK_TIMEOUT) -> Generator[None, None, None]:
    """
    OS-level file lock. Automatically released on process death.
    Uses fcntl on Unix, msvcrt on Windows.
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
                        raise TimeoutError(f"Could not acquire lock {lock_path} within {timeout}s")
                    time.sleep(0.1)
            try:
                yield
            finally:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            # Unix: fcntl blocking lock with timeout via SIGALRM
            import signal
            import fcntl
            def _timeout_handler(signum, frame):
                raise TimeoutError(f"Could not acquire lock {lock_path} within {timeout}s")
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(int(timeout))
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                signal.alarm(0)
                yield
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

