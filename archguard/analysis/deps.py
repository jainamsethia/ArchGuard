"""Dependency Health Score using pip-audit."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Vulnerability:
    """A vulnerability found by pip-audit."""

    package: str
    version: str
    vulnerability_id: str  # e.g. CVE or GHSA
    description: str


@dataclass
class DependencyHealthResult:
    """Result of the dependency analysis."""

    score: float = 100.0
    vulnerable_packages: list[Vulnerability] = field(default_factory=list)
    scanned_packages: int = 0
    skipped: bool = False
    skip_reason: str = ""
    error: str = ""

    @property
    def vulnerabilities(self) -> list[Vulnerability]:
        return self.vulnerable_packages

    @vulnerabilities.setter
    def vulnerabilities(self, value: list[Vulnerability]) -> None:
        self.vulnerable_packages = value


def _is_poetry_project(pyproject_path: Path) -> bool:
    """Detect whether a pyproject.toml uses Poetry format ([tool.poetry])."""
    try:
        content = pyproject_path.read_text(encoding="utf-8")
        return "[tool.poetry]" in content
    except Exception:
        return False


def _find_req_file(repo_root: Path) -> Path | None:
    req_files = [
        "requirements.txt",
        "requirements/base.txt",
        "requirements/prod.txt",
        "pyproject.toml",
    ]
    for req in req_files:
        if (repo_root / req).is_file():
            return repo_root / req
    return None


def _run_pip_audit(
    repo_root: Path, found_file: Path, timeout: int
) -> subprocess.CompletedProcess[str] | DependencyHealthResult:
    if found_file.name == "pyproject.toml":
        if _is_poetry_project(found_file):
            # No target can be derived for a Poetry project: pip-audit cannot
            # read poetry.lock, and invoking it bare (the previous behaviour)
            # audits whatever environment pip-audit is installed in -- which for
            # the dashboard is ArchGuard's own virtualenv. That reported
            # ArchGuard's dependencies, and its vulnerabilities, as though they
            # belonged to the analysed repository. Skipping is the honest
            # outcome; a wrong answer is worse than no answer.
            return DependencyHealthResult(
                skipped=True,
                skip_reason=(
                    "Poetry project: pip-audit cannot read poetry.lock, and "
                    "auditing without a target would scan ArchGuard's own "
                    "environment instead of this repository. Export a "
                    "requirements.txt to enable this scan."
                ),
            )
        cmd = ["pip-audit", "--format=json", str(repo_root)]
    else:
        cmd = ["pip-audit", "--format=json", "-r", str(found_file)]

    try:
        # check=False: a non-zero exit is pip-audit's normal way of reporting
        # that it found vulnerabilities; the caller inspects returncode/stdout.
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(repo_root), check=False,
        )
    except FileNotFoundError:
        return DependencyHealthResult(
            skipped=True, skip_reason="pip-audit not found in PATH."
        )
    except subprocess.TimeoutExpired:
        return DependencyHealthResult(
            skipped=True, skip_reason=f"pip-audit timed out after {timeout} seconds."
        )
    except Exception as e:
        return DependencyHealthResult(
            skipped=True, skip_reason=f"Failed to execute pip-audit: {e}"
        )


def _parse_audit_output(
    output: str,
) -> DependencyHealthResult | tuple[int, list[Vulnerability]]:
    if not output:
        return DependencyHealthResult(
            skipped=True, skip_reason="pip-audit produced no output."
        )
    try:
        data = json.loads(output)
    except json.JSONDecodeError as e:
        return DependencyHealthResult(
            skipped=True, skip_reason=f"Failed to parse pip-audit JSON output: {e}"
        )

    deps = (
        data.get("dependencies", [])
        if isinstance(data, dict) and "dependencies" in data
        else data
        if isinstance(data, list)
        else []
    )

    vulnerable_packages = []

    for dep in deps:
        for v in dep.get("vulns", []):
            vulnerable_packages.append(
                Vulnerability(
                    package=dep.get("name", "unknown"),
                    version=dep.get("version", "unknown"),
                    vulnerability_id=v.get("id", "unknown"),
                    description=v.get("description", "")
                    or v.get("aliases", [""])[0]
                    or "No description",
                )
            )
    return len(deps), vulnerable_packages


def analyze_dependencies(repo_root: Path, timeout: int | None = None) -> DependencyHealthResult:
    """Run pip-audit to calculate Dependency Health Score.

    *timeout* defaults to ``ARCHGUARD_PIP_AUDIT_TIMEOUT`` env var (default 60s).
    """
    timeout = timeout if timeout is not None else int(os.environ.get("ARCHGUARD_PIP_AUDIT_TIMEOUT", "60"))
    found_file = _find_req_file(repo_root)
    if not found_file:
        return DependencyHealthResult(
            skipped=True,
            skip_reason="No requirements file found (requirements.txt, requirements/base.txt, requirements/prod.txt, pyproject.toml).",
        )

    res = _run_pip_audit(repo_root, found_file, timeout)
    if isinstance(res, DependencyHealthResult):
        return res

    process = res
    if not process.stdout and process.stderr:
        pass

    parse_res = _parse_audit_output(process.stdout)
    if isinstance(parse_res, DependencyHealthResult):
        return parse_res

    scanned_packages, vulnerable_packages = parse_res
    score = max(0.0, 100.0 - len(vulnerable_packages) * 10.0)

    return DependencyHealthResult(
        score=score,
        vulnerable_packages=vulnerable_packages,
        scanned_packages=scanned_packages,
    )
