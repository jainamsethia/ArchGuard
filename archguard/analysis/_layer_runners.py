"""Layer 1–4 runner functions for AnalysisOrchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from archguard.analysis.layers import ViolationDetail, _get_module_paths
from archguard.utils.paths import normalize_path, path_belongs_to_module
from archguard.utils.severity import Severity


def _run_layer1(
    repo_root: Path,
    contract: dict[str, Any],
    py_files: list[Path],
    affected: dict[str, list[Path]],
    commit_sha: str,
    parse_failures: list[Any] | None = None,
) -> tuple[float, list[ViolationDetail]]:
    """Layer 1: Import boundary violations."""
    from archguard.analysis.parser import ImportParser

    if parse_failures is None:
        parse_failures = []
    violations: list[ViolationDetail] = []

    parser = ImportParser()
    modules_cfg = contract.get("modules", [])
    module_paths: dict[str, list[str]] = {
        m["name"]: _get_module_paths(m) for m in modules_cfg
    }

    # Build disallowed/allowed maps
    disallowed_map: dict[str, set[str]] = {}
    allowed_map: dict[str, set[str]] = {}
    for m in modules_cfg:
        name = m["name"]
        if "disallowed_imports" in m:
            disallowed_map[name] = set(m["disallowed_imports"])
        if "allowed_imports" in m:
            allowed_map[name] = set(m["allowed_imports"])

    total_imports = 0
    violation_count = 0

    for fpath in py_files:
        try:
            source = fpath.read_text(errors="replace")
            rel = str(fpath.relative_to(repo_root)).replace("\\", "/")
            edges = parser.parse_file(source, rel, module_paths)

            # Determine which module this file belongs to
            file_module: str | None = None
            for mod_name, paths in module_paths.items():
                for p in paths:
                    if path_belongs_to_module(rel, [p]):
                        file_module = mod_name
                        break
                if file_module:
                    break

            if file_module is None:
                continue

            for edge in edges:
                if edge.is_stdlib or edge.is_relative:
                    continue
                total_imports += 1
                root = edge.imported_module.split(".")[0]

                # Check disallowed
                if file_module in disallowed_map:
                    if root in disallowed_map[file_module]:
                        violation_count += 1
                        violations.append(
                            ViolationDetail(
                                layer=1,
                                module=file_module,
                                message=(
                                    f"Imports `{edge.imported_module}` (disallowed)"
                                ),
                                commit_sha=commit_sha[:7],
                                file_path=rel,
                                severity=Severity.CRITICAL,
                            )
                        )
                        continue

                # Check allowed (if specified, only those are permitted)
                if file_module in allowed_map:
                    if root not in allowed_map[file_module]:
                        # Check if it's within the same module
                        is_self = any(
                            path_belongs_to_module(
                                root, [normalize_path(p).split("/")[0]]
                            )
                            for p in module_paths.get(file_module, [])
                        )
                        if not is_self:
                            violation_count += 1
                            violations.append(
                                ViolationDetail(
                                    layer=1,
                                    module=file_module,
                                    message=(
                                        f"Imports `{edge.imported_module}` "
                                        f"(not in allowed_imports)"
                                    ),
                                    commit_sha=commit_sha[:7],
                                    file_path=rel,
                                    severity=Severity.CRITICAL,
                                )
                            )

        except Exception as e:
            from archguard.utils.errors import AnalysisError

            raise AnalysisError(
                f"Layer 1 analysis failed on {fpath}", cause=e
            ) from e

    parse_failures.extend(parser.parse_failures)
    return violation_count / max(total_imports, 1), violations


def _run_layer2(
    repo_root: Path,
    contract: dict[str, Any],
    affected: dict[str, list[Path]],
    commit_sha: str,
    parse_failures: list[Any] | None = None,
) -> tuple[float, list[ViolationDetail]]:
    """Layer 2: Coupling delta."""
    if parse_failures is None:
        parse_failures = []
    violations: list[ViolationDetail] = []
    from archguard.analysis.coupling import compute_coupling_delta, compute_fan_out
    from archguard.analysis.parser import ImportParser

    parser = ImportParser()
    modules_cfg = contract.get("modules", [])
    module_paths: dict[str, list[str]] = {
        m["name"]: _get_module_paths(m) for m in modules_cfg
    }
    budgets: dict[str, int] = {
        m["name"]: m.get("coupling_budget", 3) for m in modules_cfg
    }

    parse_result = parser.parse_repo(
        repo_root, module_paths, allow_partial=True
    )
    edges = parse_result.edges
    parse_failures.extend(parse_result.failures)
    max_delta = 0.0

    for mod_name in affected:
        if mod_name not in module_paths:
            continue
        fan_out = compute_fan_out(edges, mod_name, module_paths)
        budget = budgets.get(mod_name, 3)
        delta = compute_coupling_delta(fan_out, budget, mod_name)

        if delta > 0.0:
            violations.append(
                ViolationDetail(
                    layer=2,
                    module=mod_name,
                    message=f"fan_out={fan_out} exceeds budget={budget}",
                    commit_sha=commit_sha[:7],
                    file_path="",
                    severity=Severity.HIGH,
                )
            )

        max_delta = max(max_delta, delta)

    return max_delta, violations


def _run_layer3(
    cache: Any,
    contract: dict[str, Any],
    affected: dict[str, list[Path]],
    py_files: list[Path],
    commit_sha: str,
    repo_root: Path,
) -> tuple[float, dict[str, float], list[ViolationDetail]]:
    """Layer 3: Semantic drift."""
    violations: list[ViolationDetail] = []
    from archguard.analysis.semantic import SemanticAnalyzer

    analyzer = SemanticAnalyzer(cache)
    modules_cfg = contract.get("modules", [])
    thresholds: dict[str, float] = {
        m["name"]: m.get("semantic_drift_threshold", 0.25) for m in modules_cfg
    }

    max_drift = 0.0
    module_drifts: dict[str, float] = {}
    for mod_name, files in affected.items():
        try:
            result = analyzer.compute_drift(mod_name, files, repo_root)
            module_drifts[mod_name] = result.drift_score
            if result.drift_score > thresholds.get(mod_name, 0.25):
                violations.append(
                    ViolationDetail(
                        layer=3,
                        module=mod_name,
                        message=(
                            f"semantic drift {result.drift_score:.2f} "
                            f"exceeds threshold "
                            f"{thresholds.get(mod_name, 0.25):.2f}"
                        ),
                        commit_sha=commit_sha[:7],
                        file_path="",
                        severity=Severity.LOW,
                    )
                )
            max_drift = max(max_drift, result.drift_score)
        except RuntimeError:
            raise
        except Exception as e:
            from archguard.utils.errors import AnalysisError

            raise AnalysisError(
                f"Layer 3 analysis failed on module {mod_name}", cause=e
            ) from e

    return max_drift, module_drifts, violations


def _run_layer4(
    repo_root: Path,
    cache: Any,
    contract: dict[str, Any],
    affected: dict[str, list[Path]],
    commit_sha: str,
) -> tuple[float, list[ViolationDetail]]:
    """Layer 4: Duplication analysis."""
    violations: list[ViolationDetail] = []
    from archguard.analysis.duplication import DuplicationAnalyzer

    analyzer = DuplicationAnalyzer(cache)
    modules_cfg = contract.get("modules", [])
    thresholds: dict[str, float] = {
        m["name"]: m.get("duplication_threshold", 0.5) for m in modules_cfg
    }
    max_agg = 0.0

    for mod_name, files in affected.items():
        rel_files = [
            str(f.relative_to(repo_root)).replace("\\", "/")
            if f.is_absolute()
            else str(f).replace("\\", "/")
            for f in files
        ]
        try:
            result = analyzer.analyze_module(mod_name, rel_files)
            if result.aggregate_score > 0.0 and not result.skipped:
                # Collect file information from the matches
                match_details = []
                for m in result.matches[
                    :3
                ]:  # limit to top 3 to avoid huge messages
                    src_file = m.source_function.split("::")[0]
                    tgt_file = m.matched_function.split("::")[0]
                    match_details.append(f"{src_file} <-> {tgt_file}")

                details_str = ", ".join(match_details)
                if len(result.matches) > 3:
                    details_str += "..."

                threshold = thresholds.get(mod_name, 0.5)
                sev = (
                    Severity.MEDIUM
                    if result.aggregate_score >= threshold
                    else Severity.LOW
                )

                violations.append(
                    ViolationDetail(
                        layer=4,
                        module=mod_name,
                        message=(
                            f"duplication score {result.aggregate_score:.2f} "
                            f"(matches found in: {details_str})"
                        ),
                        commit_sha=commit_sha[:7],
                        file_path="",
                        severity=sev,
                    )
                )
            max_agg = max(max_agg, result.aggregate_score)
        except RuntimeError:
            raise
        except Exception as e:
            from archguard.utils.errors import AnalysisError

            raise AnalysisError(
                f"Layer 4 analysis failed on module {mod_name}", cause=e
            ) from e

    return max_agg, violations
