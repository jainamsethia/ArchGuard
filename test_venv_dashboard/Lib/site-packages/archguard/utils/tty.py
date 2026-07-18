"""TTY and CI environment detection utilities."""

import os
import sys


def is_tty() -> bool:
    """Return True if both stdin and stdout are connected to a terminal."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def is_ci() -> bool:
    """Return True if running inside a CI environment."""
    return bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))
