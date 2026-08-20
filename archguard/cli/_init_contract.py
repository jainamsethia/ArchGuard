from __future__ import annotations

import contextlib
import math
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import typer
import yaml
from rich.console import Console
from rich.prompt import Prompt

from archguard.config import EMBEDDING_CACHE_FILE
from archguard.contract.validator import validate_contract
from archguard.contract.writer import _infer_path, _model_weights_version
from archguard.utils.output import vprint

_console: Console = Console()


def _phase4_embeddings(
    communities: dict[str, list[str]],
    repo_root: Path,
    python_files: list[str],
    no_llm: bool = False,
) -> dict[str, Any]:
    """Phase 4: Embedding Centroid Computation."""
    if no_llm:
        return {
            "modules_embedded": len(communities),
            "total_functions_embedded": 0,
            "model_name": "none",
        }

    try:
        from sentence_transformers import SentenceTransformer  # lazy import
    except ImportError:
        return {
            "modules_embedded": len(communities),
            "total_functions_embedded": 0,
            "model_name": "none",
        }

    import hashlib

    import numpy as np

    from archguard.cache.db import EmbeddingDB

    model = SentenceTransformer("all-MiniLM-L6-v2")
    db_path = repo_root / EMBEDDING_CACHE_FILE

    total_embedded = 0

    with EmbeddingDB(db_path) as db:
        _console.print(
            f"  [green][OK] Found {len(python_files)} Python files[/green]\n"
            f"  [green][OK] Detected {len(communities)} primary modules[/green]\n"
        )

        choices = {"1": "strict", "2": "lenient", "3": "ci", "4": "custom"}
        answer = Prompt.ask(
            "Which profile would you like to use?\n"
            "1. strict - Production-grade enforcement (Coupling <= 5, Duplication <= 5%, Cohesion >= 80%, Health >= 85%)\n"
            "2. lenient - Minimal enforcement (Coupling <= 15, Duplication <= 20%, Cohesion >= 50%, Health >= 60%)\n"
            "3. ci - Balanced CI enforcement (Coupling <= 10, Duplication <= 10%, Cohesion >= 65%, Health >= 75%)\n"
            "4. custom - I'll set thresholds manually",
            choices=["1", "2", "3", "4"],
            default="1",
        )
        profile_name = choices[answer]

        contract: dict[str, Any] = {
            "version": "3.0",
        }

        if profile_name != "custom":
            contract["profile"] = profile_name

        contract["modules"] = []

        for module_name, files in communities.items():
            texts: list[str] = []
            file_paths: list[str] = []

            for f in files:
                full_path = repo_root / f
                try:
                    content = full_path.read_text(errors="replace")
                    texts.append(content)
                    file_paths.append(f)
                except OSError:
                    continue

            if not texts:
                continue

            # Encode in batches
            embeddings = model.encode(texts, batch_size=32)

            # Store individual embeddings
            now_iso = datetime.now(UTC).isoformat()
            for i, (fp, text) in enumerate(zip(file_paths, texts, strict=True)):
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                emb_bytes = np.array(embeddings[i], dtype=np.float32).tobytes()

                db._conn.execute(
                    """INSERT OR REPLACE INTO embeddings
                       (file_path, function_name, embedding,
                        content_hash, model_name, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        fp,
                        "__module__",
                        emb_bytes,
                        content_hash,
                        "all-MiniLM-L6-v2",
                        now_iso,
                    ),
                )

            # Compute centroid
            centroid = np.mean(embeddings, axis=0)
            centroid_bytes = np.array(centroid, dtype=np.float32).tobytes()
            centroid_hash = hashlib.sha256(centroid_bytes).hexdigest()

            db._conn.execute(
                """INSERT OR REPLACE INTO module_centroids
                   (module_name, centroid, content_hash, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (module_name, centroid_bytes, centroid_hash, now_iso),
            )

            db._conn.commit()
            total_embedded += len(texts)

    return {
        "modules_embedded": len(communities),
        "total_functions_embedded": total_embedded,
        "model_name": "all-MiniLM-L6-v2",
    }


def _contract_module_paths(communities: dict[str, list[str]]) -> dict[str, list[str]]:
    """The module->paths mapping the generated contract will actually carry.

    Each module is addressed by the single prefix ``_infer_path`` derives, which
    is exactly what ``_get_module_paths`` hands Layer 2 at analysis time.
    """
    return {name: [_infer_path(files)] for name, files in communities.items()}


def _compute_fan_outs(
    communities: dict[str, list[str]],
    repo_root: Path,
) -> dict[str, int]:
    """Compute fan_out_at_init for each module using the import parser.

    Deliberately measures against the *contract's* module paths (one inferred
    prefix per module) and via ``parse_repo``, mirroring what Layer 2 does at
    analysis time. Measuring against the parent directories of each community's
    files instead -- the previous behaviour -- counted a different set of source
    files, so the ``fan_out_at_init`` recorded in the contract disagreed with the
    fan-out the module was then graded on by 2-3x on real repositories (httpie
    recorded 21, graded on 11). The recorded number is presented to users as
    evidence, so it has to be the number that was actually used.
    """
    from archguard.analysis.coupling import compute_fan_out
    from archguard.analysis.parser import ImportParser

    module_paths = _contract_module_paths(communities)

    # parse_repo, not a hand-rolled walk over python_files: it applies the same
    # skip-list and failure handling the analysis-time parse does.
    parser = ImportParser()
    edges = parser.parse_repo(repo_root, module_paths, allow_partial=True).edges

    return {
        name: compute_fan_out(edges, name, module_paths) for name in communities
    }


def _write_summary(
    repo_root: Path,
    phase1_data: dict[str, Any],
    phase2_data: dict[str, Any],
    phase3_data: dict[str, Any],
    phase4_data: dict[str, Any],
    output_path: Path,
    communities: dict[str, list[str]],
) -> None:
    """Write .archguard-init-summary.md with all 6 sections."""
    now = datetime.now(UTC).isoformat()

    module_rows: list[str] = []
    for name, files in communities.items():
        path = _infer_path(files)
        module_rows.append(f"| {name} | {len(files)} | {path} |")

    coherence_section = "(none)"
    warnings = phase3_data.get("coherence_warnings", [])
    if warnings:
        coherence_section = "\n".join(f"- {w}" for w in warnings)

    summary = (
        f"# ArchGuard Init Summary\n"
        f"Generated: {now}\n"
        f"\n"
        f"## 1. Repository Overview\n"
        f"- Files scanned: {phase1_data.get('total_files', 0)}\n"
        f"- Lines of code: {phase1_data.get('total_loc', 0)}\n"
        f"- Commits analyzed: {phase2_data.get('commit_count', 0)}\n"
        f"\n"
        f"## 2. Communities Detected\n"
        f"| Module | Files | Paths |\n"
        f"|--------|-------|-------|\n"
    )
    for row in module_rows:
        summary += row + "\n"

    summary += (
        f"\n"
        f"## 3. Embedding Model\n"
        f"- Model: {phase4_data.get('model_name', 'all-MiniLM-L6-v2')}\n"
        f"- Functions embedded: {phase4_data.get('total_functions_embedded', 0)}\n"
        f"\n"
        f"## 4. Coherence Warnings\n"
        f"{coherence_section}\n"
        f"\n"
        f"## 5. Contract Written\n"
        f"- Path: {output_path}\n"
        f"- Modules: {len(communities)}\n"
        f"- fail_threshold: 0.75\n"
        f"\n"
        f"## 6. Next Steps\n"
        f"Run `archguard analyze` on a pull request to detect architectural drift.\n"
    )

    summary_path = repo_root / ".archguard-init-summary.md"
    summary_path.write_text(summary, encoding="utf-8")


def _generate_and_write_contract(
    communities: dict[str, list[str]],
    fan_outs: dict[str, int],
    repo_root: Path,
    output: Path,
    llm_init: bool,
    ctx: typer.Context,
    fallback_used: bool = False,
    fallback_reason: str = "",
    threshold_profile: str | None = None,
) -> int:
    """Generates YAML contract, optionally using LLM, and writes it to output path.
    Returns number of modules written.

    Thresholds come from one of two policies:

    ``threshold_profile=None`` (the default, and what ``archguard init`` uses)
        Budgets are derived from the fan-out measured during *this* run:
        ``max(3, ceil(fan_out * 1.5))``. This is a "do not get worse than today"
        baseline -- deliberately self-referential, because the team generating
        the contract will be enforcing it against their own future changes.

    ``threshold_profile="ci"`` (or another name from ``archguard.profiles``)
        Budgets come from fixed policy instead. Required for one-off analysis of
        a repository nobody is going to enforce this contract against: grading a
        repo against thresholds derived from its own current state is
        tautological, so it can only ever pass.
    """
    use_fixed_thresholds = threshold_profile is not None

    # Generate Louvain contract dictionary
    louvain_modules = []
    for name, files in communities.items():
        fan_out = fan_outs.get(name, 0)
        module: dict[str, Any] = {
            "name": name,
            "path": _infer_path(files),
            # Kept in both modes: this is a *measurement*, not a threshold, and
            # is useful context even when it is not what the module is graded on.
            "fan_out_at_init": fan_out,
        }
        if not use_fixed_thresholds:
            module["coupling_budget"] = max(3, math.ceil(fan_out * 1.5))
            module["semantic_drift_threshold"] = 0.25
        # else: left unset on purpose -- apply_profile() below fills these in,
        # and it only populates keys that are absent.
        louvain_modules.append(module)

    louvain_contract: dict[str, object] = {
        "version": "3.0",
        "model_weights_version": _model_weights_version(),
        "generated_at": datetime.now(UTC).isoformat(),
        # NOTE: consumers (e.g. dashboard/pipeline_adapter.py) detect the
        # heuristic by testing for the substring "fallback" -- keep it present.
        "generated_by": (
            "archguard init (directory heuristic fallback"
            + (f": {fallback_reason}" if fallback_reason else "")
            + ")"
        )
        if fallback_used
        else "archguard init",
        "modules": louvain_modules,
        "fail_threshold": 0.75,
        "warn_threshold": 0.50,
    }

    if use_fixed_thresholds:
        from archguard.profiles.defaults import apply_profile

        # Circular module dependencies are the one wrong-direction-import signal
        # that needs no human-authored policy: if lib imports extra and extra
        # imports lib, the cycle is a defect whatever the intended layering.
        #
        # Note what this deliberately does NOT do: synthesise `disallowed_imports`
        # entries. A cycle proves at least one edge in it is wrong, but not
        # *which* one -- naming a specific edge as forbidden would be a guess
        # presented as a rule, which is the failure mode this whole path exists
        # to avoid. The fitness function reports the cycle as a whole instead,
        # naming the real path it found.
        louvain_contract["fitness_functions"] = [
            {
                "name": "no_circular_deps",
                "rule": "graph.cycles == 0",
                "severity": "critical",
                "rationale": (
                    "Circular dependencies between modules make them impossible "
                    "to build, test, or reason about independently."
                ),
            }
        ]

        # Recorded so downstream consumers (and the dashboard) can state what the
        # score was actually graded against rather than presenting a bare number.
        louvain_contract["profile"] = threshold_profile
        # apply_profile also derives fail_threshold from the profile's
        # min_health_score; warn_threshold must stay strictly below it or every
        # WARN-band run would be reported as a pass.
        apply_profile(louvain_contract, str(threshold_profile))
        # apply_profile always writes a float here; the cast is only needed
        # because louvain_contract is a heterogeneous dict[str, object].
        fail_t = cast(float, louvain_contract.get("fail_threshold", 0.75))
        louvain_contract["warn_threshold"] = round(fail_t / 2.0, 4)

    final_contract = louvain_contract

    if llm_init:
        _console.print("[bold blue]Phase 5: LLM-driven contract generation[/bold blue]")
        try:
            import asyncio

            from archguard.contract.llm_inference import (
                _merge_contracts,
                generate_contract_from_llm,
            )

            llm_contract = asyncio.run(generate_contract_from_llm(repo_root))
            final_contract = _merge_contracts(louvain_contract, llm_contract)
            _console.print(
                "[green]Successfully merged LLM-driven boundaries with Louvain budgets.[/green]"
            )
        except Exception as e:
            _console.print(
                f"[yellow]LLM generation failed: {e}. Using Louvain only.[/yellow]"
            )

    # Validate and Write
    validate_contract(final_contract)

    dir_path = output.parent
    dir_path.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".yml", dir=str(dir_path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            yaml.dump(final_contract, tmp, default_flow_style=False, sort_keys=False)
        os.replace(tmp_name, str(output))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise

    vprint(f"Contract written to [green]{output}[/green]", ctx)

    return len(cast(list[Any], final_contract.get("modules", [])))
