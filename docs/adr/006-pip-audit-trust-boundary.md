# ADR-006: pip-audit trust boundary

**Status:** Accepted

**Deciders:** ArchGuard Engineering

**Date:** 2026-07-21

## Context

`archguard/analysis/deps.py::analyze_dependencies()` invokes the `pip-audit`
CLI tool as a subprocess with arguments derived from the repository's
filesystem layout:

```python
cmd = ["pip-audit", "--format=json", "-r", str(found_file)]
subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(repo_root))
```

`found_file` is one of `requirements.txt`, `requirements/base.txt`,
`requirements/prod.txt`, or `pyproject.toml`, resolved via
`_find_req_file()`.

The `repo_root` is supplied by the caller. In the dashboard path
(`pipeline_adapter.py`), this originates from an authenticated user's repo URL
which has been cloned via `git clone --depth=1`. In the CLI path, it comes
from `--repo` which defaults to `Path.cwd()`.

## Threat model

| Threat | Scenario | Severity |
|--------|----------|----------|
| Malicious `requirements.txt` | A repo clones a file named `requirements.txt` that is a symlink to `/etc/passwd`. `pip-audit -r` reads the target, exposing file content in error messages or output. | Medium |
| Argument injection via filename | A repo contains a file named `requirements.txt -o payload.sh` — shell injection via whitespace in filename. Mitigated by `subprocess.run` with a *list* (not a shell string), so each argument is passed verbatim to the OS. | Low |
| `pyproject.toml` Poetry mode | Poetry projects skip `-r` and run `pip-audit --format=json` without a target, scanning the entire environment. A malicious `pyproject.toml` cannot alter this behaviour. | Low |
| Symlink to FIFO/device | A repo contains a `requirements.txt` symlink to a named pipe or `/dev/zero`, causing `pip-audit` to block indefinitely. Mitigated by the 60-second subprocess timeout. | Low |

## Decision

**Accept the current trust boundary:** the repo under analysis is treated as
read-only input data, not as a trusted peer.

Justification:
- The repo is cloned by ArchGuard itself (`git clone --depth=1`), which
  guarantees a real git tree — not arbitrary filesystem paths.
- The subprocess is invoked with a *list of strings* (not a shell command),
  eliminating shell injection.
- The subprocess has a `timeout=` of 60 seconds, preventing infinite blocks
  from FIFO/device symlinks.
- `pip-audit` is a read-only scanner; it never modifies the repo or the
  filesystem outside `/tmp`.

## Consequences

- A malicious repo containing `requirements.txt` → symlink→`/etc/passwd` could
  leak file contents through `pip-audit` error messages. This is the accepted
  risk: the analysis runs in an ephemeral workspace (dashboard) or the user's
  own CWD (CLI), and `pip-audit` — not ArchGuard — is the one reading the
  file. The information is returned to the same user who provided the repo.
- If a future version adds `--output` or `--write` flags, this ADR must be
  revisited with a denial-list of dangerous flags.
- No sandboxing (seccomp, Landlock, Windows Job Objects) is applied. Add when
  ArchGuard deploys to a multi-tenant environment.
