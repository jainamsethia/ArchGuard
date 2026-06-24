"""archguard report — generates an interactive HTML dashboard."""

from __future__ import annotations

import typing
import json
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from importlib import resources

import typer
from rich.console import Console

from archguard.analysis.layers import AnalysisOrchestrator
from archguard.analysis.parser import ImportParser
from archguard.config import AUDIT_LOG_FILENAME

report_app: typer.Typer = typer.Typer(
    name="report",
    help="Generate an interactive standalone HTML architecture report.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

_console = Console()


def _read_template() -> str:
    """Read the bundled report_template.html using importlib.resources."""
    try:
        # Fallback for Python versions or paths
        ref = resources.files("archguard.templates").joinpath("report_template.html")
        return ref.read_text("utf-8")
    except Exception:
        # Development fallback
        fallback = Path(__file__).parent.parent / "templates" / "report_template.html"
        if fallback.exists():
            return fallback.read_text("utf-8")
        raise RuntimeError("Could not locate report_template.html")


def _get_trend_data() -> typing.Any:
    """Read audit log and return last 10 scores and labels."""
    log_path = Path(AUDIT_LOG_FILENAME)
    scores = []
    labels = []
    if log_path.exists():
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                runs = []
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if event.get("event") == "analysis_run":
                            runs.append(event)
                    except json.JSONDecodeError as e:
                        import logging

                        logging.getLogger(__name__).warning(
                            f"Non-critical failure parsing log line: {e}"
                        )

                # Get last 10
                for r in runs[-10:]:
                    ts_str = r.get("timestamp")
                    if not ts_str:
                        continue
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    labels.append(dt.strftime("%b %d %H:%M"))
                    score = float(
                        r.get(
                            "score",
                            r.get("archdebt", {}).get("composite_score", 0.0) * 100,
                        )
                    )
                    scores.append(score)
        except Exception as e:
            import logging

            logging.getLogger(__name__).error(
                f"Failed generating trend data: {e}"
            )
            raise typer.Exit(1)
    return {"labels": labels, "scores": scores}


def _build_graph_data(
    repo_root: Path, module_paths: dict[str, list[str]]
) -> typing.Any:
    """Build nodes and edges for vis.js by parsing imports."""
    parser = ImportParser()
    edges_raw = parser.parse_repo(repo_root, module_paths)

    nodes = []
    # Build unique nodes from the modules defined in the contract
    for mod_name in module_paths:
        nodes.append(
            {"id": mod_name, "label": mod_name.split(".")[-1], "group": mod_name}
        )

    edges = []
    # Resolve edge imports back to their modules
    # This is a simplified approach mimicking the coupling analyzer
    from archguard.utils.paths import path_belongs_to_module

    edges_list = getattr(edges_raw, "edges", edges_raw)
    for edge in edges_list:  # type: ignore
        if edge.is_stdlib or edge.is_relative:
            continue

        # Find source module
        src_mod = None
        for m, paths in module_paths.items():
            if path_belongs_to_module(edge.source_file, paths):
                src_mod = m
                break

        # Find target module (simplified: check if imported_module matches)
        tgt_mod = None
        import_as_path = edge.imported_module.replace(".", "/")
        for m, paths in module_paths.items():
            if path_belongs_to_module(import_as_path, paths) or any(
                path_belongs_to_module(p, [import_as_path]) for p in paths
            ):
                tgt_mod = m
                break

        if src_mod and tgt_mod and src_mod != tgt_mod:
            # avoid duplicates
            edges.append({"from": src_mod, "to": tgt_mod})

    # Deduplicate edges
    unique_edges = []
    seen = set()
    for e in edges:
        sig = f"{e['from']}->{e['to']}"
        if sig not in seen:
            seen.add(sig)
            unique_edges.append(e)

    return {"nodes": nodes, "edges": unique_edges}


@report_app.callback(invoke_without_command=True)
def report_cmd(
    ctx: typer.Context,
    output: Path = typer.Option(
        Path("report.html"), "--output", "-o", help="Output HTML file path."
    ),
    root: Path = typer.Option(Path("."), "--root", "-r", help="Repository root path."),
    contract: Path = typer.Option(
        None, "--contract", "-c", help="Path to contract file (optional)."
    ),
    open_browser: bool = typer.Option(
        False, "--open", help="Open the report in the default browser."
    ),
) -> None:
    """Generate a standalone HTML report with dependency graphs and trends."""
    try:
        from archguard.utils.validation import (
            validate_repo_path,
            validate_output_path,
            PathTraversalError,
        )
        from archguard.config import EXIT_CONFIG_ERROR

        root = validate_repo_path(root)
        if output is not None:
            output = validate_output_path(output)
    except PathTraversalError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(EXIT_CONFIG_ERROR)

    _console.print("[bold blue]Generating ArchGuard Report...[/bold blue]")

    # 1. Run Analysis
    orchestrator = AnalysisOrchestrator(root)
    # Get all python files
    py_files = list(root.rglob("*.py"))

    # We pass all files as "changed" to ensure full baseline analysis for the report
    try:
        result = orchestrator.run(
            changed_files=py_files, commit_sha=AnalysisOrchestrator.get_commit_sha(root)
        )
    except RuntimeError as exc:
        if "ML dependencies" in str(exc):
            _console.print(
                "[bold red]Missing Dependencies[/bold red]\n"
                "Layer 3 or 4 requires ML libraries.\n"
                "Install them with: pip install archguard[ml]\n"
                "Or add skip_layers: [semantic, duplication] to .archguard.yml"
            )
            raise typer.Exit(2)
        raise

    modules_cfg = orchestrator.contract.get("modules", [])
    module_paths = {m["name"]: m.get("paths", []) for m in modules_cfg}

    # 2. Extract Data
    summary_data = {
        "score": result.archdebt.composite_score * 100,
        "grade": result.archdebt.band.value,
        "violation_count": len(result.violations),
        "module_count": len(module_paths),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    violations_data = [
        {
            "layer": v.layer,
            "module": getattr(v, "module", ""),
            "message": getattr(v, "message", ""),
            "severity": str(getattr(v, "severity", "low")),
        }
        for v in result.violations
    ]

    graph_data = _build_graph_data(root, module_paths)
    trend_data = _get_trend_data()

    # 3. Inject into Template
    template = _read_template()
    html_output = (
        template.replace("{{ SUMMARY_JSON }}", json.dumps(summary_data))
        .replace("{{ VIOLATIONS_JSON }}", json.dumps(violations_data))
        .replace("{{ GRAPH_DATA_JSON }}", json.dumps(graph_data))
        .replace("{{ TREND_DATA_JSON }}", json.dumps(trend_data))
    )

    # 4. Write and Open
    output.write_text(html_output, encoding="utf-8")
    _console.print(
        f"[green]Report successfully generated at [bold]{output}[/bold][/green]"
    )

    if open_browser:
        webbrowser.open(f"file://{output.resolve()}")
