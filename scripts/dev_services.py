#!/usr/bin/env python
"""Keep the local PostgreSQL and Redis reachable for the length of a session.

On a Windows developer machine these run inside WSL2, and WSL2 shuts an idle VM
down -- taking both services with it. Measured on WSL 2.5.10: the VM was gone
after 100 seconds of inactivity, and `wsl -l -v` reported "Stopped" while
`systemctl` inside the distro reported the services active, because the query
itself cold-booted the VM in order to answer.

The consequences were not obvious from the failures they caused:

  * a full pytest run stalled indefinitely -- once the VM went, every remaining
    test paid a 3-second Redis connection timeout, turning three minutes into
    the better part of an hour;
  * DB-backed tests failed with "alembic upgrade failed", which reads like a
    broken migration;
  * `scripts/smoke_test.sh` reported four endpoint 500s that looked like
    application bugs.

`.wslconfig`'s `vmIdleTimeout` does not control this here. The key is accepted
under `[wsl2]` and the VM still stops, both at `-1` and at a week in
milliseconds. What does work is an attached session: with one open, the VM
stayed Running and both ports stayed reachable across the same idle period.

pytest holds its own session automatically (see `_services_reachable` in
tests/db_fixtures.py). This script is for everything else -- a dev server, the
smoke script, a Playwright run:

    python scripts/dev_services.py            # hold until Ctrl-C
    python scripts/dev_services.py --check    # one-shot health check

A no-op anywhere that is not Windows, or that points its services at something
other than loopback, so it is safe to call unconditionally from a Makefile.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

WSL_DISTRO = os.environ.get("ARCHGUARD_WSL_DISTRO", "Ubuntu")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def configured_services() -> list[tuple[str, str, int]]:
    """(name, host, port) for each service the environment points at."""
    found = []
    for name, env in (("PostgreSQL", "DATABASE_URL"), ("Redis", "REDIS_URL")):
        url = os.environ.get(env, "").strip()
        if not url:
            continue
        parsed = urlparse(url)
        if parsed.hostname and parsed.port:
            found.append((name, parsed.hostname, parsed.port))
    return found


def reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wsl_exe() -> Path | None:
    """The wsl.exe this machine has, or None if WSL is not the host."""
    if sys.platform != "win32":
        return None
    path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wsl.exe"
    return path if path.exists() else None


def hold_open() -> subprocess.Popen[bytes] | None:
    """Start an attached WSL session, which is what stops the VM idling out."""
    wsl = wsl_exe()
    services = configured_services()
    if wsl is None or not services:
        return None
    if not all(host in LOOPBACK for _, host, _ in services):
        # Pointing somewhere else -- a container, a remote host. Not ours to hold.
        return None
    try:
        return subprocess.Popen(
            [str(wsl), "-d", WSL_DISTRO, "--", "sleep", "86400"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except OSError:
        return None


def wait_until_up(deadline: float = 20.0) -> bool:
    """Poll until every configured service answers, or give up."""
    services = configured_services()
    if not services:
        return True
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if all(reachable(h, p) for _, h, p in services):
            return True
        time.sleep(0.5)
    return False


def report() -> bool:
    services = configured_services()
    if not services:
        print("No DATABASE_URL or REDIS_URL configured; nothing to check.")
        return True
    ok = True
    for name, host, port in services:
        up = reachable(host, port)
        print(f"  {'OK  ' if up else 'DOWN'}  {name} at {host}:{port}")
        ok = ok and up
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="report reachability and exit, without holding anything open",
    )
    args = parser.parse_args()

    if args.check:
        return 0 if report() else 1

    keeper = hold_open()
    if keeper is None:
        print("Not a WSL-hosted setup; nothing to hold.")
        return 0 if report() else 1

    if not wait_until_up():
        print("Services did not come up. Check `wsl -l -v` and systemctl inside "
              f"{WSL_DISTRO}.", file=sys.stderr)
        report()
        keeper.terminate()
        return 1

    report()
    print(f"\nHolding {WSL_DISTRO} open. Ctrl-C to release.")
    try:
        keeper.wait()
    except KeyboardInterrupt:
        print("\nReleasing.")
    finally:
        keeper.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
