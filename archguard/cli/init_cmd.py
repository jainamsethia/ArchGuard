"""archguard init — full 5-phase onboarding wizard."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.prompt import Prompt

from archguard.config import CHECKPOINTS_DIR, EMBEDDING_CACHE_FILE
from archguard.utils.errors import format_error, format_warning
from archguard.utils.output import vprint
from archguard.utils.tty import is_tty

init_app: typer.Typer = typer.Typer(
    name="init",
    help="Initialize ArchGuard in a repository.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

_console: Console = Console()

# Directories to exclude from repository scan
_EXCLUDE_DIRS: frozenset[str] = frozenset({
    "__pycache__", ".venv", "venv", ".git", "node_modules",
    ".tox", "dist", "build",
})


# ------------------------------------------------------------------
# Checkpoint helpers
# ------------------------------------------------------------------

def save_checkpoint(repo_root: Path, phase: int, data: dict[str, Any]) -> None:
    """Save checkpoint JSON for a completed phase."""
    cp_dir = repo_root / CHECKPOINTS_DIR
    cp_dir.mkdir(parents=True, exist_ok=True)

    checkpoint = {
        "phase": phase,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }

    path = cp_dir / f"phase_{phase}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(checkpoint, f, indent=2, default=str)


def load_checkpoint(repo_root: Path, phase: int) -> dict[str, Any] | None:
    """Load checkpoint JSON for a specific phase.  Returns None if not found."""
    path = repo_root / CHECKPOINTS_DIR / f"phase_{phase}.json"
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
        return None


def latest_completed_phase(repo_root: Path) -> int:
    """Return the highest completed phase number, or 0 if none."""
    for phase in range(5, 0, -1):
        if load_checkpoint(repo_root, phase) is not None:
            return phase
    return 0


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def count_loc(file_path: Path) -> int:
    """Count non-blank lines of code."""
    try:
        return sum(
            1 for line in file_path.read_text(errors="replace").splitlines()
            if line.strip()
        )
    except OSError:
        return 0

def _fallback_directory_modules(repo_path: Path) -> dict[str, list[str]]:
    """Detect modules from directory structure when commit history is sparse."""
    modules: dict[str, list[Path]] = {}
    for py_file in repo_path.rglob("*.py"):
        if any(part.startswith(".") for part in py_file.parts):
            continue  # Skip hidden dirs
            
        relative = py_file.relative_to(repo_path)
        parts = relative.parts
        
        if "test" in parts or "tests" in parts:
            modules.setdefault("tests", []).append(relative)
            continue
            
        # Use top-level package as module name
        if len(parts) >= 2:
            module_name = parts[0]
        else:
            module_name = "root"
        modules.setdefault(module_name, []).append(relative)
        
    # Merge tiny modules into 'misc'
    small_modules = [k for k, v in modules.items() if len(v) < 3]
    if small_modules:
        misc_files = []
        for k in small_modules:
            misc_files.extend(modules.pop(k))
        if misc_files:
            modules["misc"] = misc_files
            
    if not modules:
        return {"main": [str(p.relative_to(repo_path)).replace("\\", "/") for p in repo_path.rglob("*.py") if not any(part.startswith(".") for part in p.parts)]}
        
    return {k: [str(p).replace("\\", "/") for p in v] for k, v in modules.items()}

# ------------------------------------------------------------------
# Phase implementations
# ------------------------------------------------------------------

def _phase1_scan(repo_root: Path) -> dict[str, Any]:
    """Phase 1: Repository Scan."""
    python_files: list[str] = []

    for py_file in sorted(repo_root.rglob("*.py")):
        if any(skip in py_file.parts for skip in _EXCLUDE_DIRS):
            continue
        try:
            rel = str(py_file.relative_to(repo_root)).replace("\\", "/")
            python_files.append(rel)
        except ValueError:
            continue

    total_loc = sum(count_loc(repo_root / f) for f in python_files)

    return {
        "total_files": len(python_files),
        "total_loc": total_loc,
        "python_files": python_files,
    }


def _phase2_commits(repo_root: Path) -> dict[str, Any]:
    """Phase 2: Commit History Analysis."""
    from pydriller import Repository  # lazy import
    import networkx as nx  # lazy import

    try:
        repo = Repository(str(repo_root))
        commits = list(repo.traverse_commits())
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Git history extraction failed: {e}")
        commits = []

    if len(commits) > 1000:
        commits = commits[-500:]

    graph = nx.Graph()

    for commit in commits:
        py_files = [
            m.filename
            for m in commit.modified_files
            if m.filename and m.filename.endswith(".py")
        ]
        for i, f1 in enumerate(py_files):
            for f2 in py_files[i + 1:]:
                if graph.has_edge(f1, f2):
                    graph[f1][f2]["weight"] += 1
                else:
                    graph.add_edge(f1, f2, weight=1)

    return {
        "commit_count": len(commits),
        "graph_nodes": graph.number_of_nodes(),
        "graph_edges": graph.number_of_edges(),
        "graph_data": nx.node_link_data(graph),
    }


def _phase3_communities(
    graph_data: dict[str, Any],
    repo_root: Path,
    python_files: list[str],
    commit_count: int,
    min_history: int,
) -> dict[str, Any]:
    """Phase 3: Louvain Community Detection."""
    import networkx as nx  # lazy import
    from archguard.analysis.community import detect_communities, get_seed_from_repo

    graph = nx.node_link_graph(graph_data)
    seed = get_seed_from_repo(repo_root)

    if commit_count < min_history:
        _console.print(
            f"\n[yellow]⚠ Insufficient commit history ({commit_count} < {min_history}). "
            "Falling back to directory-structure-based module detection.[/yellow]"
        )
        communities = _fallback_directory_modules(repo_root)
    else:
        communities = detect_communities(graph, seed=seed, min_community_size=2)

        # Fallback: if no communities detected, create a single module
        if not communities or len(communities) < 2:
            _console.print(
                "\n[yellow]⚠ Insufficient community diversity detected in co-change graph. "
                "Falling back to directory-structure-based module detection.[/yellow]"
            )
            communities = _fallback_directory_modules(repo_root)

    return {
        "seed": seed,
        "num_communities": len(communities),
        "communities": communities,
        "coherence_warnings": [],
    }


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
            default="1"
        )
        profile_name = choices[answer]

        contract: dict[str, Any] = {
            "schema_version": "3.0",
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
                    (fp, "__module__", emb_bytes,
                     content_hash, "all-MiniLM-L6-v2", now_iso),
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


def _interactive_review(
    communities: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Interactive module review for TTY sessions."""
    from archguard.contract.writer import _infer_path

    reviewed: dict[str, list[str]] = {}

    for name, files in communities.items():
        _console.print()
        _console.print(f"[bold]Module: {name}[/bold] ({len(files)} files)")
        path = _infer_path(files)
        _console.print(f"Paths: {path}")

        response = typer.prompt("Accept this module? [Y/n/rename]", default="Y")
        response = response.strip().lower()

        if response in ("y", ""):
            reviewed[name] = files
        elif response == "n":
            _console.print(f"[dim]Skipped {name}[/dim]")
        elif response.startswith("rename"):
            new_name = typer.prompt("New name")
            reviewed[new_name.strip()] = files
        else:
            reviewed[name] = files

    return reviewed


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
    from archguard.contract.writer import _infer_path

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


# ------------------------------------------------------------------
# Main command
# ------------------------------------------------------------------

@init_app.callback(invoke_without_command=True)
def init_command(
    ctx: typer.Context,
    repo: Path = typer.Option(
        Path("."), "--repo", help="Path to the repository root.",
    ),
    confirm_all: bool = typer.Option(
        False, "--confirm-all", help="Skip all interactive prompts.",
    ),
    force_ci: bool = typer.Option(
        False, "--force-ci", help="Bypass shallow clone check in CI.",
    ),
    resume: bool = typer.Option(
        False, "--resume", help="Resume from last saved checkpoint.",
    ),
    output: Path | None = typer.Option(
        None, "--output", help="Write contract to this path.",
    ),
    no_llm: bool = typer.Option(
        False, "--no-llm", help="Skip LLM API calls entirely.",
    ),
    min_history_commits: int = typer.Option(
        5,
        "--min-history-commits",
        help="Minimum commits needed for co-change analysis (default: 5). "
             "Below this, falls back to directory-structure detection.",
    ),
    monorepo: bool = typer.Option(
        False, "--monorepo", help="Initialize each sub package separately",
    ),
    llm_init: bool = typer.Option(
        False, "--llm-init", help="Use Claude to generate contract from code structure (requires ANTHROPIC_API_KEY)"
    ),
) -> None:
    """Initialize ArchGuard in a repository with 5-phase onboarding."""
    try:
        from archguard.utils.validation import validate_repo_path, validate_output_path, PathTraversalError
        from archguard.config import EXIT_CONFIG_ERROR
        repo = validate_repo_path(repo)
        if output is not None:
            output = validate_output_path(output)
    except PathTraversalError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR)

    repo_root = repo.resolve()

    if monorepo:
        from archguard.utils.monorepo import detect_subpackages
        packages = detect_subpackages(repo_root)
        if not packages:
            typer.echo("No sub-packages found. Run without --monorepo.")
            raise typer.Exit(1)
            
        has_error = False
        for pkg in packages:
            _console.print(f"\n[bold magenta]Initializing sub-package: {pkg.name}[/bold magenta]")
            try:
                init_command(
                    ctx=ctx,
                    repo=pkg,
                    confirm_all=confirm_all,
                    force_ci=force_ci,
                    resume=resume,
                    output=pkg / ".archguard.yml",
                    no_llm=no_llm,
                    min_history_commits=min_history_commits,
                    monorepo=False,
                    llm_init=llm_init,
                )
            except typer.Exit as e:
                if e.exit_code != 0:
                    has_error = True
            except Exception as e:
                _console.print(f"[red]Error initializing {pkg.name}: {e}[/red]")
                has_error = True
                
        if has_error:
            raise typer.Exit(1)
        return

    if output is None:
        output = repo_root / ".archguard.yml"

    # Shallow clone guard
    if os.environ.get("GITHUB_ACTIONS") and not force_ci:
        try:
            from pydriller import Repository  # lazy import

            repo_obj = Repository(str(repo_root))
            commit_count = len(list(repo_obj.traverse_commits()))
            if commit_count < 100:
                _console.print(format_error(
                    "Shallow clone detected (< 100 commits). "
                    "archguard init requires full history. "
                    "Use --force-ci to bypass or fetch-depth: 0 "
                    "in your workflow."
                ))
                raise typer.Exit(1)
        except ImportError as e:
            import logging
            logging.getLogger(__name__).warning(f"Non-critical failure importing pydriller: {e}")

    # Non-TTY auto-confirm
    if not is_tty() and not confirm_all:
        _console.print(format_warning(
            "Non-interactive terminal detected. "
            "Running with --confirm-all behavior."
        ))
        confirm_all = True

    # Determine start phase
    start_phase = 1
    if resume:
        start_phase = latest_completed_phase(repo_root) + 1
        if start_phase > 5:
            vprint("[green]All phases already completed.[/green]", ctx)
            return
        vprint(f"[blue]Resuming from phase {start_phase}...[/blue]", ctx)

    # Phase data holders
    phase1_data: dict[str, Any] = {}
    phase2_data: dict[str, Any] = {}
    phase3_data: dict[str, Any] = {}
    phase4_data: dict[str, Any] = {}

    # Load existing checkpoint data if resuming
    if start_phase > 1:
        cp1 = load_checkpoint(repo_root, 1)
        if cp1:
            phase1_data = cp1["data"]
    if start_phase > 2:
        cp2 = load_checkpoint(repo_root, 2)
        if cp2:
            phase2_data = cp2["data"]
    if start_phase > 3:
        cp3 = load_checkpoint(repo_root, 3)
        if cp3:
            phase3_data = cp3["data"]
    if start_phase > 4:
        cp4 = load_checkpoint(repo_root, 4)
        if cp4:
            phase4_data = cp4["data"]

    try:
        # PHASE 1 — Repository Scan
        if start_phase <= 1:
            vprint("[bold cyan][1/5] Scanning repository...[/bold cyan]", ctx)
            phase1_data = _phase1_scan(repo_root)

            if phase1_data["total_files"] == 0:
                _console.print(format_error(
                    "No Python files found in repository."
                ))
                raise typer.Exit(1)

            vprint(
                f"Found {phase1_data['total_files']} Python files | "
                f"{phase1_data['total_loc']:,} LOC", ctx
            )

            # Runtime estimate for large repos
            if phase1_data["total_loc"] > 50_000:
                est = phase1_data["total_loc"] // 10_000 * 2
                vprint(
                    f"[blue]Estimated init time: ~{est} minutes "
                    f"for {phase1_data['total_loc']:,} LOC[/blue]", ctx
                )

            save_checkpoint(repo_root, 1, phase1_data)

        # PHASE 2 — Commit History Analysis
        if start_phase <= 2:
            vprint(
                "[bold cyan][2/5] Analyzing commit history...[/bold cyan]", ctx
            )
            phase2_data = _phase2_commits(repo_root)

            vprint(
                f"Processed {phase2_data['commit_count']} commits | "
                f"{phase2_data['graph_edges']} co-change pairs", ctx
            )

            save_checkpoint(repo_root, 2, phase2_data)

        # PHASE 3 — Louvain Community Detection
        if start_phase <= 3:
            vprint(
                "[bold cyan][3/5] Detecting module communities..."
                "[/bold cyan]", ctx
            )
            
            quiet = ctx.obj.get("quiet", False)
            use_rich = is_tty() and not quiet
            
            if use_rich:
                from rich.progress import Progress, SpinnerColumn, TextColumn
                with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=_console) as p:
                    p.add_task("Detecting communities...", total=None)
                    phase3_data = _phase3_communities(
                        phase2_data["graph_data"],
                        repo_root,
                        phase1_data["python_files"],
                        commit_count=phase2_data.get("commit_count", 0),
                        min_history=min_history_commits,
                    )
            else:
                phase3_data = _phase3_communities(
                    phase2_data["graph_data"],
                    repo_root,
                    phase1_data["python_files"],
                    commit_count=phase2_data.get("commit_count", 0),
                    min_history=min_history_commits,
                )

            seed_hex = hex(phase3_data["seed"])
            vprint(
                f"Found {phase3_data['num_communities']} communities | "
                f"Seed: {seed_hex}", ctx
            )

            save_checkpoint(repo_root, 3, phase3_data)

        communities: dict[str, list[str]] = phase3_data["communities"]
        
        if ctx.obj.get("verbose"):
            for name, files in communities.items():
                for f in files:
                    vprint(f"Found file {f} assigned to module {name}", ctx, level="debug")

        # Interactive review (TTY only, skipped if --confirm-all)
        if not confirm_all and is_tty():
            communities = _interactive_review(communities)
            if not communities:
                _console.print(format_error(
                    "All modules were skipped. Cannot generate contract."
                ))
                raise typer.Exit(1)
            phase3_data["communities"] = communities
            phase3_data["num_communities"] = len(communities)

        # PHASE 4 — Embedding Centroid Computation
        if start_phase <= 4:
            vprint(
                "[bold cyan][4/5] Computing semantic embeddings..."
                "[/bold cyan]", ctx
            )
            phase4_data = _phase4_embeddings(
                communities, repo_root, phase1_data["python_files"], no_llm=no_llm
            )

            vprint(
                f"Embedded {phase4_data['modules_embedded']} modules | "
                f"{phase4_data['total_functions_embedded']} functions | "
                f"model: {phase4_data['model_name']}", ctx
            )

            save_checkpoint(repo_root, 4, phase4_data)

        # Compute fan_out_at_init
        fan_outs = _compute_fan_outs(
            communities, phase1_data["python_files"], repo_root,
        )

        # PHASE 5 — YAML Contract Write
        if start_phase <= 5:
            vprint(
                "[bold cyan][5/5] Writing contract...[/bold cyan]", ctx
            )

            from archguard.contract.writer import write_contract
            import tempfile
            import yaml
            from archguard.contract.validator import validate_contract
            import math
            from archguard.contract.writer import _infer_path, _model_weights_version
            
            # Generate Louvain contract dictionary
            louvain_modules = []
            for name, files in communities.items():
                fan_out = fan_outs.get(name, 0)
                budget = max(3, math.ceil(fan_out * 1.5))
                louvain_modules.append({
                    "name": name,
                    "paths": [_infer_path(files)],
                    "fan_out_at_init": fan_out,
                    "coupling_budget": budget,
                    "semantic_drift_threshold": 0.25,
                })
            
            louvain_contract = {
                "schema_version": "3.0",
                "model_weights_version": _model_weights_version(),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "generated_by": "archguard init",
                "modules": louvain_modules,
                "fail_threshold": 0.75,
                "warn_threshold": 0.50,
            }
            
            final_contract = louvain_contract
            
            if llm_init:
                _console.print("[bold blue]Phase 5: LLM-driven contract generation[/bold blue]")
                try:
                    import asyncio
                    from archguard.contract.llm_inference import generate_contract_from_llm, _merge_contracts
                    
                    llm_contract = asyncio.run(generate_contract_from_llm(repo_root))
                    final_contract = _merge_contracts(louvain_contract, llm_contract)
                    _console.print("[green]Successfully merged LLM-driven boundaries with Louvain budgets.[/green]")
                except Exception as e:
                    _console.print(f"[yellow]LLM generation failed: {e}. Using Louvain only.[/yellow]")

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
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise

            vprint(f"Contract written to [green]{output}[/green]", ctx)

            save_checkpoint(repo_root, 5, {
                "output_path": str(output),
                "modules_written": len(final_contract.get("modules", [])),
            })

        # Write summary
        _write_summary(
            repo_root=repo_root,
            phase1_data=phase1_data,
            phase2_data=phase2_data,
            phase3_data=phase3_data,
            phase4_data=phase4_data,
            output_path=output,
            communities=communities,
        )

        vprint("", ctx)
        vprint(
            "[bold green]\u2713 ArchGuard initialization complete![/bold green]", ctx
        )

    except typer.Exit:
        raise
    except Exception as exc:
        _console.print(format_error(f"Phase failed: {exc}"))
        _console.print(
            "[yellow]Use --resume to continue from the last "
            "completed phase.[/yellow]"
        )
        raise typer.Exit(1) from exc
