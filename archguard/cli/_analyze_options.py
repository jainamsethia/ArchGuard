import typer
from pathlib import Path
from dataclasses import dataclass


@dataclass
class AnalyzeOptions:
    ctx: typer.Context
    repo: Path
    pr_number: int | None = None
    repo_slug: str | None = None
    profile: str | None = None
    changed_files: str | None = None
    skip_explanation: bool = False
    full: bool = False
    json_output: bool = False
    fail_on_warn: bool = False
    dry_run: bool = False
    incremental: bool = False
    no_incremental: bool = False
    no_llm: bool = False
    out_file: Path | None = None
    fail_fast: bool = False
    monorepo: bool = False
    watch: bool = False
    metrics_flag: bool = False
    verbose: bool = False
    fail_threshold: float | None = None
