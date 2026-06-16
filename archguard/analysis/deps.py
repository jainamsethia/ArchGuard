"""Dependency Health Score using pip-audit."""

from __future__ import annotations

import json
import logging
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


def analyze_dependencies(repo_root: Path, timeout: int = 60) -> DependencyHealthResult:
    """Run pip-audit to calculate Dependency Health Score."""
    
    # 1. Search for requirements files
    req_files = [
        "requirements.txt",
        "requirements/base.txt",
        "requirements/prod.txt",
        "pyproject.toml",
    ]
    
    found_file = None
    for req in req_files:
        if (repo_root / req).is_file():
            found_file = repo_root / req
            break

    if not found_file:
        return DependencyHealthResult(
            skipped=True,
            skip_reason="No requirements file found (requirements.txt, requirements/base.txt, requirements/prod.txt, pyproject.toml)."
        )

    # 2. Run pip-audit
    if found_file.name == "pyproject.toml":
        # pip-audit project-path mode requires PEP 621 [project] section.
        # Poetry projects use [tool.poetry] instead, so path mode fails with:
        #   "pyproject file pyproject.toml does not contain `project` section"
        # Detect the format and choose the right invocation.
        is_poetry = _is_poetry_project(found_file)
        if is_poetry:
            # Environment scan: audits packages installed in the active venv.
            cmd = ["pip-audit", "--format=json"]
        else:
            # PEP 621 project path scan: audits the target project directly.
            cmd = ["pip-audit", "--format=json", str(repo_root)]
    else:
        cmd = ["pip-audit", "--format=json", "-r", str(found_file)]
    
    try:
        # Note: pip-audit returns non-zero if vulnerabilities are found
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(repo_root)
        )
    except FileNotFoundError:
        return DependencyHealthResult(
            skipped=True,
            skip_reason="pip-audit not found in PATH."
        )
    except subprocess.TimeoutExpired:
        return DependencyHealthResult(
            skipped=True,
            skip_reason=f"pip-audit timed out after {timeout} seconds."
        )
    except Exception as e:
        return DependencyHealthResult(
            skipped=True,
            skip_reason=f"Failed to execute pip-audit: {e}"
        )

    output = process.stdout
    if not output and process.stderr:
        # If stdout is empty but stderr is not, it could be a fatal error
        pass
        
    if not output:
        return DependencyHealthResult(
            skipped=True,
            skip_reason="pip-audit produced no output."
        )

    # 3. Parse JSON
    try:
        data = json.loads(output)
    except json.JSONDecodeError as e:
        return DependencyHealthResult(
            skipped=True,
            skip_reason=f"Failed to parse pip-audit JSON output: {e}"
        )

    # data is typically a list of dicts:
    # [
    #   {"name": "requests", "version": "2.25.1", "vulns": [{"id": "CVE-...", "fix_versions": [...], "description": "..."}]}
    # ]
    # Wait, pip-audit 2.x JSON format:
    # {"dependencies": [{"name": "pkg", "version": "1.0", "vulns": [...]}]}
    
    # Let's handle both formats just in case
    deps = []
    if isinstance(data, dict) and "dependencies" in data:
        deps = data.get("dependencies", [])
    elif isinstance(data, list):
        deps = data
    
    vulnerable_packages = []
    scanned_packages = len(deps)
    
    for dep in deps:
        vulns = dep.get("vulns", [])
        for v in vulns:
            vulnerable_packages.append(
                Vulnerability(
                    package=dep.get("name", "unknown"),
                    version=dep.get("version", "unknown"),
                    vulnerability_id=v.get("id", "unknown"),
                    description=v.get("description", "") or v.get("aliases", [""])[0] or "No description",
                )
            )

    # 4. Calculate score
    score = max(0.0, 100.0 - len(vulnerable_packages) * 10.0)

    return DependencyHealthResult(
        score=score,
        vulnerable_packages=vulnerable_packages,
        scanned_packages=scanned_packages,
    )
