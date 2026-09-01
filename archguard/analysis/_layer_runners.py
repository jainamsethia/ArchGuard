"""Layer 1–4 runner functions for AnalysisOrchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from archguard.analysis import violation_kinds
from archguard.analysis.layers import ViolationDetail, _get_module_paths
from archguard.utils.paths import normalize_path, path_belongs_to_module
from archguard.utils.severity import Severity


def _analyze_file_imports(
    fpath: Path,
    repo_root: Path,
    parser: Any,
    module_paths: dict[str, list[str]],
    disallowed_map: dict[str, set[str]],
    allowed_map: dict[str, set[str]],
    commit_sha: str,
) -> tuple[int, int, list[ViolationDetail], str | None]:
    """Check one file against its module's import rules.

    The fourth value is the module the file was resolved to, or None. The
    caller needs it to tell "examined and compliant" from "never opened": both
    produce no violations, and only one of them is a measurement.
    """
    total_imports = 0
    violation_count = 0
    violations = []
    try:
        source = fpath.read_text(errors="replace")
        rel = str(fpath.relative_to(repo_root)).replace("\\", "/")
        edges = parser.parse_file(source, rel, module_paths)

        file_module: str | None = None
        for mod_name, paths in module_paths.items():
            for p in paths:
                if path_belongs_to_module(rel, [p]):
                    file_module = mod_name
                    break
            if file_module:
                break

        if file_module is None:
            return 0, 0, [], None

        for edge in edges:
            if edge.is_stdlib or edge.is_relative or edge.is_third_party:
                # Stdlib and relative imports are always permitted.
                # Third-party imports are not controlled by allowed_imports;
                # the field only governs cross-module boundaries between
                # project-declared modules (see .archguard.yml comments).
                continue
            total_imports += 1
            root = edge.imported_module.split(".")[0]

            if file_module in disallowed_map and root in disallowed_map[file_module]:
                violation_count += 1
                violations.append(
                    ViolationDetail(
                        layer=1,
                        module=file_module,
                        message=f"Imports `{edge.imported_module}` (disallowed)",
                        commit_sha=commit_sha[:7],
                        file_path=rel,
                        line=edge.line,
                        severity=Severity.CRITICAL,
                        kind=violation_kinds.IMPORT_BOUNDARY,
                        metrics={},
                    )
                )
                continue

            if file_module in allowed_map and root not in allowed_map[file_module]:
                is_self = root == file_module or any(
                    path_belongs_to_module(root, [normalize_path(p).split("/")[0]])
                    for p in module_paths.get(file_module, [])
                )
                if not is_self:
                    violation_count += 1
                    violations.append(
                        ViolationDetail(
                            layer=1,
                            module=file_module,
                            message=f"Imports `{edge.imported_module}` (not in allowed_imports)",
                            commit_sha=commit_sha[:7],
                            file_path=rel,
                            line=edge.line,
                            severity=Severity.CRITICAL,
                            kind=violation_kinds.IMPORT_BOUNDARY,
                            metrics={},
                        )
                    )

    except Exception as e:
        from archguard.utils.errors import AnalysisError

        raise AnalysisError(f"Layer 1 analysis failed on {fpath}", cause=e) from e

    return total_imports, violation_count, violations, file_module


def _run_layer1(
    repo_root: Path,
    contract: dict[str, Any],
    py_files: list[Path],
    affected: dict[str, list[Path]],
    commit_sha: str,
    parse_failures: list[Any] | None = None,
) -> tuple[float, list[ViolationDetail], str]:
    """Layer 1: Import boundary violations.

    Returns ``(score, violations, skip_reason)``. A non-empty reason means the
    layer examined nothing and its 0.00 is an absence of measurement rather
    than an absence of violations.

    What counts as examined here is a *file put in front of a rule*, which is
    deliberately not Layer 2's "was any module in scope". A file in a
    rule-bearing module that imports only stdlib has been opened, resolved and
    found compliant -- vacuously, but really -- so counting imports instead
    would report a genuinely clean repository as unchecked.
    """
    from archguard.analysis.parser import ImportParser

    if parse_failures is None:
        parse_failures = []
    violations: list[ViolationDetail] = []

    parser = ImportParser()
    modules_cfg = contract.get("modules", [])
    module_paths: dict[str, list[str]] = {
        m["name"]: _get_module_paths(m) for m in modules_cfg
    }

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
    examined_files = 0

    for fpath in py_files:
        t_imports, v_count, v_list, file_module = _analyze_file_imports(
            fpath,
            repo_root,
            parser,
            module_paths,
            disallowed_map,
            allowed_map,
            commit_sha,
        )
        # Examined means this file belonged to a module that declares rules for
        # it. A file in an undeclared directory was never opened against
        # anything, and a file in a module with no rules of its own had nothing
        # to be checked against.
        if file_module is not None and (
            file_module in disallowed_map or file_module in allowed_map
        ):
            examined_files += 1
        total_imports += t_imports
        violation_count += v_count
        violations.extend(v_list)

    parse_failures.extend(parser.parse_failures)

    # The existing contract-level check catches "this contract declares no
    # import rules", which is the ordinary state of an auto-generated one. It
    # cannot catch the case that matters here: rules declared, and not one of
    # the paths they are attached to matching a file in the repository. Every
    # file then resolves to no module, every call above returns early, and the
    # score is 0/max(0,1) -- a clean zero from a layer that opened nothing.
    #
    # Kept as a separate message rather than folded into the other one. "No
    # rules declared" is not worth acting on; "rules declared and none of them
    # reach a file" is a broken contract, and a reader sent looking for a
    # missing `allowed_imports` would never find it.
    skip_reason = (
        ""
        if examined_files
        else (
            "no file belongs to a module with import rules - "
            "boundaries not measured"
        )
    )

    return violation_count / max(total_imports, 1), violations, skip_reason


def _run_layer2(
    repo_root: Path,
    contract: dict[str, Any],
    affected: dict[str, list[Path]],
    commit_sha: str,
    parse_failures: list[Any] | None = None,
) -> tuple[float, list[ViolationDetail], str]:
    """Layer 2: Coupling delta.

    Returns ``(score, violations, skip_reason)``. The third value is new and is
    what the other three layers already had: a way to say the layer measured
    nothing, as opposed to measuring everything and finding nothing. Both are a
    0.00, and the composite averages the second into the score as a clean pass.
    """
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

    parse_result = parser.parse_repo(repo_root, module_paths, allow_partial=True)
    edges = parse_result.edges
    parse_failures.extend(parse_result.failures)
    max_delta = 0.0
    measured_modules = 0

    for mod_name in affected:
        if mod_name not in module_paths:
            continue
        measured_modules += 1
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
                    kind=violation_kinds.FAN_OUT,
                    metrics={"fan_out": float(fan_out), "budget": float(budget)},
                )
            )

        max_delta = max(max_delta, delta)

    # Same rule as Layers 3 and 4: one measured module means the layer produced
    # a signal, and only when none did is it reported as not run. Reached when
    # the scan has no module in scope at all, and when every name in scope is
    # one the contract does not declare paths for -- a contract whose paths
    # match nothing in the repository produces both.
    skip_reason = (
        ""
        if measured_modules
        else "no module was in scope for this scan - coupling not measured"
    )

    return max_delta, violations, skip_reason


def _run_layer3(
    cache: Any,
    contract: dict[str, Any],
    affected: dict[str, list[Path]],
    py_files: list[Path],
    commit_sha: str,
    repo_root: Path,
) -> tuple[float, dict[str, float], list[ViolationDetail], str]:
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
    skip_reason = ""
    skipped_modules = 0
    measured_modules = 0
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
                        kind=violation_kinds.SEMANTIC_DRIFT,
                        metrics={
                            "drift": float(result.drift_score),
                            "threshold": float(thresholds.get(mod_name, 0.25)),
                        },
                    )
                )
            if getattr(result, "skipped", False):
                skipped_modules += 1
                # Keep the first reason seen; they are the same cause in
                # practice (no baseline on a first scan).
                skip_reason = skip_reason or getattr(result, "skip_reason", "")
            else:
                measured_modules += 1
            max_drift = max(max_drift, result.drift_score)
        except RuntimeError:
            raise
        except Exception as e:
            from archguard.utils.errors import AnalysisError

            raise AnalysisError(
                f"Layer 3 analysis failed on module {mod_name}", cause=e
            ) from e

    # Only call the whole layer skipped when nothing was measured. If some
    # modules had a baseline and others did not, the layer did produce a real
    # signal from those that did, and flagging it "skipped" would understate
    # it. (Callers treat any non-empty reason as "this layer did not run".)
    if measured_modules:
        skip_reason = ""
    elif not skip_reason:
        # Nothing was measured and no module said why, because there were no
        # modules: the loop above never ran. An empty reason is how this
        # function says "I ran", so a layer that looked at nothing was reported
        # as a clean 0.00 and averaged into the composite as a real measurement.
        #
        # An incremental scan reaches this whenever its changed files belong to
        # no module the contract declares -- routine, since an auto-generated
        # contract names only the modules it could measure and leaves the rest
        # of the repository out. The same tree scanned in full hands this every
        # module and gets "no prior baseline" instead, so the layer is excluded
        # from the composite there and included here: two different scores for
        # one repository state, which is the thing incremental analysis is not
        # allowed to do.
        skip_reason = (
            "no module was in scope for this scan - semantic drift not measured"
        )

    return max_drift, module_drifts, violations, skip_reason


def _run_layer4(
    repo_root: Path,
    cache: Any,
    contract: dict[str, Any],
    affected: dict[str, list[Path]],
    commit_sha: str,
) -> tuple[float, list[ViolationDetail], str]:
    """Layer 4: Duplication analysis."""
    violations: list[ViolationDetail] = []
    from archguard.analysis.duplication import DuplicationAnalyzer

    analyzer = DuplicationAnalyzer(cache)
    modules_cfg = contract.get("modules", [])
    thresholds: dict[str, float] = {
        m["name"]: m.get("duplication_threshold", 0.5) for m in modules_cfg
    }
    max_agg = 0.0
    skip_reason = ""
    measured_modules = 0

    # Fill the corpus before searching it. Layer 4 matches against the
    # embeddings table, and Layer 3 only ever writes the modules that scan
    # re-analysed -- so on an incremental scan every unchanged file is missing
    # a vector, and a clone of one cannot be found however wide `affected` is.
    #
    # Per module, not one flat list, so embed_files' MAX_FILES cap keeps the
    # per-module meaning it has in Layer 3. Already-embedded files cost a
    # batched cache lookup, not a re-encode; no-ops without the ML extras.
    #
    # This loop must finish before the one below starts, and must not be merged
    # into it. Duplication is cross-module: the first module's search needs the
    # last module's vectors already in the corpus, so embedding a module just
    # before analysing it would leave every module blind to the ones after it.
    from archguard.analysis.semantic import SemanticAnalyzer

    embedder = SemanticAnalyzer(cache)
    for mod_name, files in affected.items():
        embedder.embed_files(
            files, repo_root, context=f"Duplication corpus for module {mod_name}"
        )

    for mod_name, files in affected.items():
        # Compute repo-root-relative paths. Layer 3 keys its cached embeddings
        # by repo-root-relative file path, so the file set passed here MUST use
        # the same form or _build_faiss_index's membership check finds nothing
        # (every module gets an empty embedding set -> no duplication matches).
        root_resolved = repo_root.resolve()
        rel_files = []
        for f in files:
            if f.is_absolute():
                rel_files.append(str(f.relative_to(root_resolved)).replace("\\", "/"))
                continue
            # Relative: resolve against CWD, fall back to repo-root re-rooting.
            resolved = False
            for candidate in (f, repo_root / f):
                try:
                    rel_files.append(
                        str(candidate.resolve().relative_to(root_resolved)).replace("\\", "/")
                    )
                    resolved = True
                    break
                except ValueError:
                    continue
            if not resolved:
                rel_files.append(str(f).replace("\\", "/"))
        try:
            from archguard.analysis.layers import _get_module_paths
            mod_paths = next(
                (_get_module_paths(m) for m in modules_cfg if m["name"] == mod_name), []
            )
            if not mod_paths and mod_name == "misc":
                mod_paths = ["./"]

            result = analyzer.analyze_module(mod_name, rel_files, mod_paths)
            if result.skipped:
                # The first reason, not the last. `affected` is a dict, so
                # "whichever module happened to come last" made the layer's
                # explanation depend on iteration order.
                skip_reason = skip_reason or result.skip_reason
            else:
                measured_modules += 1
            if not result.skipped and result.aggregate_score > 0.0:
                # Collect file information from the matches
                match_details = []
                for m in result.matches[:8]:  # limit to top 8 to avoid huge messages
                    src_file = m.source_function.split("::")[0]
                    tgt_file = m.matched_function.split("::")[0]
                    match_details.append(f"{src_file} <-> {tgt_file}")

                details_str = ", ".join(match_details)
                if len(result.matches) > 8:
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
                        kind=violation_kinds.DUPLICATION,
                        metrics={
                            "duplication_score": float(result.aggregate_score),
                            "threshold": float(threshold),
                            "match_count": float(len(result.matches)),
                        },
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

    # Three states, and the caller can only see two of them: it reads a
    # non-empty reason as "this layer did not run" and an empty one as "it did".
    # So the reason has to carry the distinction.
    #
    #   measured, clean      -> no reason; a real 0.00 goes into the composite
    #   nothing measurable   -> a reason naming the scope, and the layer is
    #                           reweighted out rather than scored as a pass
    #   unavailable          -> a reason from the analyzer (no ML extras, stale
    #                           cache), which is what it already produced
    #
    # Same rule as Layer 3, deliberately. One measured module means the layer
    # produced a real signal, and calling it skipped because another module had
    # nothing to index would throw that signal away. Nothing measured means the
    # 0.00 is an absence of measurement rather than an absence of duplication,
    # and reporting it as a clean pass is how a check that never ran contributes
    # a perfect score.
    if measured_modules:
        skip_reason = ""
    elif not skip_reason:
        # No modules at all: the loop never ran and no module could explain
        # itself. Reachable whenever the contract declares nothing that matches
        # a file in the repository.
        skip_reason = (
            "no module was in scope for this scan - duplication not measured"
        )

    return max_agg, violations, skip_reason
