import os
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from archguard.cli._init_checkpoints import (
    latest_completed_phase,
    load_checkpoint,
    save_checkpoint,
)
from archguard.cli._init_contract import (
    _compute_fan_outs,
    _generate_and_write_contract,
    _phase4_embeddings,
    _write_summary,
)
from archguard.cli._init_phases import (
    _phase1_scan,
    _phase2_commits,
    _phase3_communities,
)
from archguard.cli._init_wizard import _interactive_review
from archguard.utils.errors import ContractGenerationError, format_error, format_warning
from archguard.utils.output import vprint
from archguard.utils.tty import is_tty


def _run_init_cli(
    ctx: typer.Context,
    repo_root: Path,
    output: Path | None,
    confirm_all: bool,
    force_ci: bool,
    resume: bool,
    no_llm: bool,
    min_history_commits: int,
    llm_init: bool,
    wizard: bool,
    force: bool,
    _console: Console,
    threshold_profile: str | None = None,
) -> None:
    """Run the init wizard.

    *threshold_profile* selects fixed policy thresholds from
    ``archguard.profiles`` instead of the default self-referential baseline
    derived from the repository's own fan-out at generation time. The interactive
    ``archguard init`` command leaves this ``None``; the dashboard's one-off
    analysis of an arbitrary repo sets it (see
    ``dashboard/pipeline_adapter.py::_generate_contract_sync``).
    """
    if output is None:
        output = repo_root / ".archguard.yml"

    if output.exists() and not force:
        if wizard:
            response = typer.prompt(f"File {output.name} already exists. Overwrite? [y/N]", default="N")
            if response.lower() not in ("y", "yes"):
                _console.print("[dim]Init aborted.[/dim]")
                raise typer.Exit(0)
        else:
            message = format_error(f"{output.name} already exists. Use --force to overwrite or run with --wizard.")
            _console.print(message)
            raise ContractGenerationError(str(message))

    if wizard:
        confirm_all = False

    # Shallow clone guard
    if os.environ.get("GITHUB_ACTIONS") and not force_ci:
        try:
            from pydriller import Repository

            repo_obj = Repository(str(repo_root))
            commit_count = len(list(repo_obj.traverse_commits()))
            if commit_count < 100:
                message = format_error(
                    "Shallow clone detected (< 100 commits). "
                    "archguard init requires full history. "
                    "Use --force-ci to bypass or fetch-depth: 0 "
                    "in your workflow."
                )
                _console.print(message)
                raise ContractGenerationError(str(message))
        except ImportError as e:
            import logging

            logging.getLogger(__name__).warning(
                f"Non-critical failure importing pydriller: {e}"
            )

    if not is_tty() and not confirm_all:
        _console.print(
            format_warning(
                "Non-interactive terminal detected. "
                "Running with --confirm-all behavior."
            )
        )
        confirm_all = True

    start_phase = 1
    if resume:
        start_phase = latest_completed_phase(repo_root) + 1
        if start_phase > 5:
            vprint("[green]All phases already completed.[/green]", ctx)
            return
        vprint(f"[blue]Resuming from phase {start_phase}...[/blue]", ctx)

    phase1_data: dict[str, Any] = {}
    phase2_data: dict[str, Any] = {}
    phase3_data: dict[str, Any] = {}
    phase4_data: dict[str, Any] = {}

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
        if start_phase <= 1:
            vprint("[bold cyan][1/5] Scanning repository...[/bold cyan]", ctx)
            phase1_data = _phase1_scan(repo_root)

            if phase1_data["total_files"] == 0:
                message = format_error("No Python files found in repository.")
                _console.print(message)
                raise ContractGenerationError(str(message))

            vprint(
                f"Found {phase1_data['total_files']} Python files | "
                f"{phase1_data['total_loc']:,} LOC",
                ctx,
            )

            if phase1_data["total_loc"] > 50_000:
                est = phase1_data["total_loc"] // 10_000 * 2
                vprint(
                    f"[blue]Estimated init time: ~{est} minutes "
                    f"for {phase1_data['total_loc']:,} LOC[/blue]",
                    ctx,
                )

            save_checkpoint(repo_root, 1, phase1_data)

        if start_phase <= 2:
            vprint("[bold cyan][2/5] Analyzing commit history...[/bold cyan]", ctx)
            phase2_data = _phase2_commits(repo_root)

            if phase2_data.get("history_status") != "ok":
                _console.print(
                    format_warning(
                        "Commit history could not be read "
                        f"({phase2_data.get('history_error', 'unknown error')}). "
                        "Module boundaries will be guessed from directory names."
                    )
                )
            vprint(
                f"Processed {phase2_data['commit_count']} commits | "
                f"{phase2_data['graph_edges']} co-change pairs",
                ctx,
            )

            save_checkpoint(repo_root, 2, phase2_data)

        if start_phase <= 3:
            vprint("[bold cyan][3/5] Detecting module communities...[/bold cyan]", ctx)

            quiet = ctx.obj.get("quiet", False)
            use_rich = is_tty() and not quiet

            if use_rich:
                from rich.progress import Progress, SpinnerColumn, TextColumn

                with Progress(
                    SpinnerColumn(), TextColumn("{task.description}"), console=_console
                ) as p:
                    p.add_task("Detecting communities...", total=None)
                    phase3_data = _phase3_communities(
                        phase2_data.get("graph_data", {}),
                        repo_root,
                        phase1_data.get("python_files", []),
                        commit_count=phase2_data.get("commit_count", 0),
                        min_history=min_history_commits,
                        history_status=phase2_data.get("history_status", "ok"),
                        history_error=phase2_data.get("history_error", ""),
                    )
            else:
                phase3_data = _phase3_communities(
                    phase2_data.get("graph_data", {}),
                    repo_root,
                    phase1_data.get("python_files", []),
                    commit_count=phase2_data.get("commit_count", 0),
                    min_history=min_history_commits,
                    history_status=phase2_data.get("history_status", "ok"),
                    history_error=phase2_data.get("history_error", ""),
                )

            seed_hex = hex(phase3_data["seed"])
            vprint(
                f"Found {phase3_data['num_communities']} communities | "
                f"Seed: {seed_hex}",
                ctx,
            )

            save_checkpoint(repo_root, 3, phase3_data)

        communities: dict[str, list[str]] = phase3_data["communities"]

        if ctx.obj.get("verbose"):
            for name, files in communities.items():
                for f in files:
                    vprint(
                        f"Found file {f} assigned to module {name}", ctx, level="debug"
                    )

        if not confirm_all and is_tty():
            communities = _interactive_review(communities)
            if not communities:
                message = format_error("All modules were skipped. Cannot generate contract.")
                _console.print(message)
                raise ContractGenerationError(str(message))
            phase3_data["communities"] = communities
            phase3_data["num_communities"] = len(communities)

        if start_phase <= 4:
            vprint("[bold cyan][4/5] Computing semantic embeddings...[/bold cyan]", ctx)
            phase4_data = _phase4_embeddings(
                communities,
                repo_root,
                phase1_data.get("python_files", []),
                no_llm=no_llm,
            )

            vprint(
                f"Embedded {phase4_data['modules_embedded']} modules | "
                f"{phase4_data['total_functions_embedded']} functions | "
                f"model: {phase4_data['model_name']}",
                ctx,
            )

            save_checkpoint(repo_root, 4, phase4_data)

        fan_outs = _compute_fan_outs(communities, repo_root)

        if start_phase <= 5:
            vprint("[bold cyan][5/5] Writing contract...[/bold cyan]", ctx)

            modules_written = _generate_and_write_contract(
                communities=communities,
                fan_outs=fan_outs,
                repo_root=repo_root,
                output=output,
                llm_init=llm_init,
                ctx=ctx,
                fallback_used=phase3_data.get("fallback_used", False),
                fallback_reason=phase3_data.get("fallback_reason", ""),
                threshold_profile=threshold_profile,
            )

            save_checkpoint(
                repo_root,
                5,
                {
                    "output_path": str(output),
                    "modules_written": modules_written,
                },
            )

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
        vprint("[bold green]ArchGuard initialization complete![/bold green]", ctx)

    except typer.Exit:
        raise
    except Exception as exc:
        _console.print(format_error(f"Phase failed: {exc}"))
        _console.print(
            "[yellow]Use --resume to continue from the last completed phase.[/yellow]"
        )
        raise typer.Exit(1) from exc
