"""The credential a private clone needs must not leak (P3-3).

These are the tests that matter most in this feature. An installation token is
live for an hour and opens whatever the installation covers, so the two ways it
escapes -- the process command line, and the git error text handed back to the
caller -- each get a test that fails if the credential reappears there.
"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import pytest

from archguard.dashboard.workspace import _clone_repo, _credential_env, _redact

TOKEN = "ghs_liveinstallationtoken"


def test_the_token_is_not_in_the_command_line(monkeypatch, tmp_path):
    """argv is world-readable on a shared host; a URL credential lands there."""
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    import asyncio

    asyncio.run(
        _clone_repo(
            "https://github.com/acme/secret.git", tmp_path / "repo", "HEAD", token=TOKEN
        )
    )

    joined = " ".join(seen["cmd"])
    assert TOKEN not in joined
    # The Basic form is what actually travels, so check for it too.
    basic = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    assert basic not in joined


def test_the_token_reaches_git_through_the_environment(monkeypatch, tmp_path):
    """Having kept it out of argv, it still has to get there somehow."""
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    import asyncio

    asyncio.run(
        _clone_repo(
            "https://github.com/acme/secret.git", tmp_path / "repo", "HEAD", token=TOKEN
        )
    )

    env = seen["env"]
    assert env is not None
    assert env["GIT_CONFIG_COUNT"] == "1"
    basic = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    assert env["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {basic}"


def test_a_public_clone_still_inherits_the_environment(monkeypatch, tmp_path):
    """env=None is load-bearing on Windows: the module comments say why."""
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    import asyncio

    asyncio.run(
        _clone_repo("https://github.com/pallets/flask.git", tmp_path / "repo", "HEAD")
    )

    assert seen["env"] is None


def test_the_credential_is_scoped_to_one_origin():
    """Unscoped, the header would follow a redirect to another host."""
    env = _credential_env("https://github.com/acme/secret.git", TOKEN)
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"


def test_git_may_not_prompt_for_a_password():
    """Without this a repository the App cannot read blocks until the timeout."""
    env = _credential_env("https://github.com/acme/secret.git", TOKEN)
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_a_failing_clone_does_not_put_the_token_in_the_exception(monkeypatch, tmp_path):
    """git echoes the remote's response, and this message reaches the caller."""

    def fake_run(cmd, **kwargs):
        # git really does echo back the credential it was given in some
        # authentication failures.
        basic = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
        stderr = (
            f"fatal: unable to access: Authorization: Basic {basic} "
            f"rejected for {TOKEN}"
        ).encode()
        return subprocess.CompletedProcess(cmd, 128, b"", stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)

    import asyncio

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(
            _clone_repo(
                "https://github.com/acme/secret.git",
                tmp_path / "repo",
                "HEAD",
                token=TOKEN,
            )
        )

    message = str(caught.value)
    assert TOKEN not in message
    basic = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    assert basic not in message
    # Still useful: the operator needs to know what git actually said.
    assert "unable to access" in message


def test_redaction_covers_both_shapes_of_the_secret():
    basic = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    text = f"raw {TOKEN} and encoded {basic}"
    cleaned = _redact(text, TOKEN)
    assert TOKEN not in cleaned
    assert basic not in cleaned


def test_redaction_is_a_no_op_without_a_token():
    assert _redact("nothing secret here", None) == "nothing secret here"


@pytest.mark.asyncio
async def test_temp_workspace_passes_the_token_through(monkeypatch, tmp_path):
    """The context manager is the only caller; a dropped token means a 404."""
    from archguard.dashboard import workspace

    seen: dict = {}

    async def fake_clone(url, dest, branch, *, token=None):
        seen["token"] = token
        Path(dest).mkdir(parents=True)

    monkeypatch.setattr(workspace, "_clone_repo", fake_clone)

    async with workspace.temp_workspace(
        "https://github.com/acme/secret.git", token=TOKEN
    ):
        pass

    assert seen["token"] == TOKEN
