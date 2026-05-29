import sys
import re

with open("archguard/cli/analyze_cmd.py", "r", encoding="utf-8") as f:
    content = f.read()

dataclass_def = """from dataclasses import dataclass, field

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

"""

content = content.replace(
    "from archguard.profiles.defaults import apply_profile\n",
    "from archguard.profiles.defaults import apply_profile\n\n" + dataclass_def
)

caller_old = """        _analyze_command_impl(ctx, repo, pr, repo_slug, profile, changed_files, skip_explanation, full, json_output, fail_on_warn, dry_run, incremental, no_incremental, no_llm, out_file, fail_fast)"""
caller_new = """        opts = AnalyzeOptions(
            ctx=ctx,
            repo=repo,
            pr_number=pr,
            repo_slug=repo_slug,
            profile=profile,
            changed_files=changed_files,
            skip_explanation=skip_explanation,
            full=full,
            json_output=json_output,
            fail_on_warn=fail_on_warn,
            dry_run=dry_run,
            incremental=incremental,
            no_incremental=no_incremental,
            no_llm=no_llm,
            out_file=out_file,
            fail_fast=fail_fast,
        )
        result = _analyze_command_impl(opts)
        if result != 0:
            raise typer.Exit(result)"""
content = content.replace(caller_old, caller_new)

sig_old = """def _analyze_command_impl(ctx, repo, pr, repo_slug, profile, changed_files, skip_explanation, full, json_output, fail_on_warn, dry_run, incremental, no_incremental, no_llm, out_file, fail_fast):"""
sig_new = """def _analyze_command_impl(opts: AnalyzeOptions) -> int:"""
content = content.replace(sig_old, sig_new)

parts = content.split("def _analyze_command_impl(opts: AnalyzeOptions) -> int:")
pre = parts[0]
body = parts[1]

repls = {
    "no_llm": "opts.no_llm",
    "skip_explanation": "opts.skip_explanation",
    "repo.resolve()": "opts.repo.resolve()",
    "repo_slug": "opts.repo_slug",
    "pr": "opts.pr_number",
    "profile": "opts.profile",
    "changed_files": "opts.changed_files",
    "json_output": "opts.json_output",
    "fail_on_warn": "opts.fail_on_warn",
    "dry_run": "opts.dry_run",
    "incremental": "opts.incremental",
    "no_incremental": "opts.no_incremental",
    "out_file": "opts.out_file",
    "fail_fast": "opts.fail_fast",
    "ctx": "opts.ctx",
}

for k, v in repls.items():
    if k == "repo.resolve()":
        body = body.replace(k, v)
    else:
        body = re.sub(r'\b' + k + r'\b', v, body)

# Fixups for partial matches
body = body.replace("_get_opts.pr_number_number", "_get_pr_number")
body = body.replace("opts.opts.ctx", "opts.ctx")
body = body.replace("opts.opts.pr_number", "opts.pr_number")
body = body.replace("opts.opts.repo_slug", "opts.repo_slug")
body = body.replace("opts.pr_numberogress", "progress")
body = body.replace("apply_opts.profile", "apply_profile")
body = body.replace("opts.profile_to_use", "profile_to_use")

# Change raise typer.Exit and sys.exit to return
body = body.replace("raise typer.Exit(EXIT_VIOLATION)", "return EXIT_VIOLATION")
body = body.replace("raise typer.Exit(EXIT_OK)", "return EXIT_OK")
body = body.replace("raise typer.Exit(2)", "return 2")
body = body.replace("sys.exit(1)", "return 1")
body = body.replace("raise typer.Exit(1)", "return 1")

# Add return 0 at the end
body = body + "\n    return 0\n"

content = pre + "def _analyze_command_impl(opts: AnalyzeOptions) -> int:" + body

with open("archguard/cli/analyze_cmd.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Done")
