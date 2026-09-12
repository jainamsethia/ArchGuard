"""A clone timeout has to actually stop the clone.

`subprocess.run(capture_output=True, timeout=...)` does not bound `git clone`,
and the way it fails is quiet. On a timeout it kills the process it started and
then drains the pipes. `git clone` spawns git-remote-https and index-pack, and
they inherit the write end of that pipe -- so the drain waits for an EOF that
cannot arrive while a grandchild holds it open. The call blocks forever,
TimeoutError is never raised, and the download carries on.

Found in production shape: a clone of torvalds/linux still running twelve
minutes into a 120-second timeout, its direct child already killed, two
index-pack processes at 1.1 GB each still going, the worker silent, and every
queued job behind it frozen.

The test below reproduces exactly that structure -- a child that outlives its
parent's death by holding the inherited handle -- without needing git or a
network.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time

import pytest

#: How long the grandchild outlives a kill of its parent. Only needs to be far
#: enough above the timeout below that overshoot cannot be scheduling noise.
_GRANDCHILD_SECONDS = 20
_TIMEOUT_SECONDS = 2

# A parent that spawns a longer-lived grandchild inheriting its stdout, then
# sleeps. Killing the parent alone leaves the grandchild holding the pipe --
# which is exactly what `git clone` does with git-remote-https and index-pack.
_PARENT = textwrap.dedent(
    f"""
    import subprocess, sys, time
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep({_GRANDCHILD_SECONDS})"],
                     stdout=sys.stdout, stderr=sys.stderr)
    time.sleep({_GRANDCHILD_SECONDS})
    """
)


def test_subprocess_run_does_not_bound_a_process_that_has_children():
    """Pin the standard-library behaviour the workspace code works around.

    `run()` raises TimeoutExpired as documented -- but only after blocking for
    as long as the grandchild lives, because the drain it performs after
    killing the child waits on a pipe that grandchild still holds. A caller
    reading the docstring would reasonably expect to be released at the
    timeout, and that expectation is what cost a worker twelve minutes.

    If a future Python closes the write end before draining, this fails and
    `_terminate_process_tree` can go.
    """
    if sys.platform != "win32":
        pytest.skip("the pipe-inheritance hang is reproducible on Windows")

    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run(
            [sys.executable, "-c", _PARENT],
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    elapsed = time.monotonic() - start

    assert elapsed >= _GRANDCHILD_SECONDS * 0.7, (
        f"subprocess.run returned after {elapsed:.1f}s for a "
        f"{_TIMEOUT_SECONDS}s timeout, so it no longer waits on the "
        "grandchild's pipe -- the workaround in workspace.py can be removed"
    )


def test_a_timed_out_clone_raises_rather_than_hanging(tmp_path, monkeypatch):
    """The behaviour that matters: bounded, and it says why.

    Points the cloner at a URL that cannot complete quickly and gives it one
    second. What must not happen is that this test hangs.
    """
    import archguard.dashboard.workspace as ws

    monkeypatch.setattr(ws, "CLONE_TIMEOUT_SECONDS", 1)

    import asyncio

    start = time.monotonic()
    with pytest.raises((TimeoutError, RuntimeError)) as excinfo:
        # 203.0.113.0/24 is TEST-NET-3: reserved for documentation, routed
        # nowhere. git will sit waiting for a connection that never lands,
        # which is the shape of a clone that overruns without failing.
        asyncio.run(
            ws._clone_repo(
                "https://203.0.113.1/nothing/here.git", tmp_path / "dest", "HEAD"
            )
        )
    elapsed = time.monotonic() - start

    assert elapsed < 90, f"the clone was not bounded; it took {elapsed:.0f}s"
    if isinstance(excinfo.value, TimeoutError):
        assert "timed out" in str(excinfo.value).lower()
