# ADR-006: pip-audit trust boundary

**Status:** Accepted (revised 2026-08-19 — decision reversed for project targets)

**Deciders:** ArchGuard Engineering

**Date:** 2026-07-21, revised 2026-08-19

## Context

`archguard/analysis/deps.py::analyze_dependencies()` invokes the `pip-audit`
CLI as a subprocess against a repository ArchGuard does not control. In the
dashboard path (`routes/runs.py` → `GET /api/v1/deps`) that repository is an
arbitrary GitHub URL submitted by a user and cloned into a temp workspace. In
the CLI path it is `--repo`, defaulting to the user's own cwd.

`_find_req_file()` resolves one of `requirements.txt`,
`requirements/base.txt`, `requirements/prod.txt`, or `pyproject.toml`.

## The revision: project targets are code execution

The original version of this ADR accepted the trust boundary on the grounds
that "pip-audit is a read-only scanner". That is true of `pip-audit -r
<file>`, which only parses a pinned requirements file. It is **not** true of
`pip-audit <path>`, which was the branch taken for a non-Poetry
`pyproject.toml`:

```python
cmd = ["pip-audit", "--format=json", str(repo_root)]   # removed
```

Auditing a *project* requires pip-audit to resolve its dependencies, and
resolving a source tree builds it — running `setup.py` or the PEP 517 backend
declared in the repository's own `pyproject.toml`. A repository that declares
a malicious build backend therefore achieved arbitrary code execution on the
analysis host, as the server user, simply by being submitted to the dashboard
and having its dependency panel opened.

## Decision

**Only already-pinned requirement files are ever passed to pip-audit**, via
`-r`. Anything that would require resolving or building the analysed tree is
skipped with an explicit reason shown to the user:

| Input | Behaviour |
|-------|-----------|
| `requirements*.txt` | Audited with `pip-audit -r <file>` |
| `pyproject.toml` (Poetry) | Skipped — pip-audit cannot read `poetry.lock`, and a bare invocation would audit ArchGuard's own virtualenv and report its packages as the analysed repo's |
| `pyproject.toml` (PEP 621) | Skipped — resolving it executes the repository's build backend |
| no requirements file | Skipped |

A missing answer is correct here. A wrong answer (ArchGuard's own
vulnerabilities attributed to someone else's repo) and a dangerous answer
(RCE) are both worse than "export a requirements.txt to enable this scan".

## Residual threats

| Threat | Scenario | Severity | Status |
|--------|----------|----------|--------|
| Argument injection via filename | A file named `requirements.txt -o payload` | Low | Mitigated: `subprocess.run` takes a *list*, so each argument reaches the OS verbatim; no shell is involved |
| Symlinked `requirements.txt` | Symlink to `/etc/passwd`; pip-audit reads the target and may echo content in an error | Medium | Accepted: the output returns to the same user who supplied the repo |
| Symlink to FIFO/device | Causes pip-audit to block indefinitely | Low | Mitigated by `ARCHGUARD_PIP_AUDIT_TIMEOUT` (60s default) |

## Consequences

- Dependency Health is unavailable for projects that do not commit a
  requirements file. This is a deliberate reduction in coverage.
- If a future change adds a project/path target back, or adds `--fix`,
  `--output` or any flag that writes, this ADR must be revisited first.
- No sandboxing (seccomp, Landlock, Windows Job Objects) is applied. The
  build-execution vector is closed by not invoking it rather than by
  containing it; add containment before running untrusted resolution at all.

## Test coverage

`tests/unit/test_dep_health.py::test_pep621_pyproject_is_skipped_never_resolved`
and `::test_poetry_project_is_skipped_not_audited_against_the_wrong_env` both
assert `subprocess.run` is never called, so a regression re-introducing either
target fails the suite rather than silently reopening the hole.
