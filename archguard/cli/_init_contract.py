from __future__ import annotations
import os
import tempfile
import yaml
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
import typer
from rich.console import Console
from rich.prompt import Prompt
from archguard.config import EMBEDDING_CACHE_FILE
from archguard.contract.writer import _infer_path, _model_weights_version
from archguard.contract.validator import validate_contract
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
            f"  [green]✔ Found {len(python_files)} Python files[/green]\n"
            f"  [green]✔ Detected {len(communities)} primary modules[/green]\n"
        )

        choices = {"1": "strict", "2": "lenient", "3": "ci", "4": "custom"}
        answer = Prompt.ask(
            "Which profile would you like to use?\n"
            "1. strict — Production-grade enforcement\n"
            "2. lenient — Minimal enforcement\n"
            "3. ci — Balanced CI enforcement\n"
            "4. custom — I'll set thresholds manually",
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
            now_iso = datetime.now(timezone.utc).isoformat()
            for i, (fp, text) in enumerate(zip(file_paths, texts)):
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


def _compute_fan_outs(
    communities: dict[str, list[str]],
    python_files: list[str],
    repo_root: Path,
) -> dict[str, int]:
    """Compute fan_out_at_init for each module using the import parser."""
    from archguard.analysis.parser import ImportParser
    from archguard.analysis.coupling import compute_fan_out

    parser = ImportParser()
    # Use individual files as module paths for fan-out computation
    temp_module_paths: dict[str, list[str]] = {
        name: files for name, files in communities.items()
    }

    edges = []
    for f in python_files:
        try:
            source = (repo_root / f).read_text(errors="replace")
            edges.extend(parser.parse_file(source, f, temp_module_paths))
        except OSError:
            continue

    fan_outs: dict[str, int] = {}
    for name in communities:
        fan_outs[name] = compute_fan_out(edges, name, temp_module_paths)

    return fan_outs


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
    now = datetime.now(timezone.utc).isoformat()

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
) -> int:
    """Generates YAML contract, optionally using LLM, and writes it to output path.
    Returns number of modules written.
    """
    # Generate Louvain contract dictionary
    louvain_modules = []
    for name, files in communities.items():
        fan_out = fan_outs.get(name, 0)
        budget = max(3, math.ceil(fan_out * 1.5))
        louvain_modules.append(
            {
                "name": name,
                "path": _infer_path(files),
                "fan_out_at_init": fan_out,
                "coupling_budget": budget,
                "semantic_drift_threshold": 0.25,
            }
        )

    louvain_contract = {
        "version": "3.0",
        "model_weights_version": _model_weights_version(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "archguard init",
        "modules": louvain_modules,
        "fail_threshold": 0.75,
        "warn_threshold": 0.50,
    }

    final_contract = louvain_contract

    if llm_init:
        _console.print(
            "[bold blue]Phase 5: LLM-driven contract generation[/bold blue]"
        )
        try:
            import asyncio
            from archguard.contract.llm_inference import (
                generate_contract_from_llm,
                _merge_contracts,
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
            yaml.dump(
                final_contract, tmp, default_flow_style=False, sort_keys=False
            )
        os.replace(tmp_name, str(output))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    vprint(f"Contract written to [green]{output}[/green]", ctx)

    return len(cast(list[Any], final_contract.get("modules", [])))
