"""File-based locking with timeout for ArchGuard.

Uses ``msvcrt`` on Windows and ``fcntl`` on Unix/macOS.
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Generator

LOCK_TIMEOUT_SECONDS: float = 30.0


class LockTimeoutError(Exception):
    """Raised when a file lock cannot be acquired within timeout."""


def _try_lock(f: IO[bytes]) -> bool:
    """Attempt a non-blocking lock on *f*.  Returns True on success."""
    if sys.platform == "win32":
        import msvcrt

        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    else:
        import fcntl  # type: ignore[import-not-found]

        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False


def _unlock(f: IO[bytes]) -> None:
    """Release the lock on *f*."""
    if sys.platform == "win32":
        import msvcrt

        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl  # type: ignore[import-not-found]

        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


@contextmanager
def acquire_lock(
    lock_path: Path,
    timeout: float = LOCK_TIMEOUT_SECONDS,
) -> Generator[None, None, None]:
    """Acquire a file lock with timeout.

    * Creates the lock file if it does not exist.
    * Attempts a non-blocking lock every 0.1 s up to *timeout*.
    * On timeout raises :class:`LockTimeoutError`.
    * Releases the lock on exit (even on exception).
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure the file has at least 1 byte (required by msvcrt on Windows)
    if not lock_path.exists() or lock_path.stat().st_size == 0:
        lock_path.write_bytes(b"\x00")

    f: IO[bytes] = open(lock_path, "r+b")  # noqa: SIM115
    try:
        deadline = time.monotonic() + timeout
        while not _try_lock(f):
            if time.monotonic() >= deadline:
                f.close()
                raise LockTimeoutError(
                    f"Could not acquire lock at {lock_path} after {timeout}s. "
                    f"Another archguard process may be running. "
                    f"Delete {lock_path} manually if stale."
                )
            time.sleep(0.1)
        yield
    finally:
        if not f.closed:
            _unlock(f)
            f.close()
