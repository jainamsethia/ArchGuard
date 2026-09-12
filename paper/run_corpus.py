"""Run the real ArchGuard pipeline over a corpus of public Python repositories.

Records only what the system reports. No derived or estimated values.
Output: a JSON file of per-repository results for the paper's Results section.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPOS = [
    "https://github.com/pallets/itsdangerous",
    "https://github.com/psf/cachecontrol",
    "https://github.com/pallets/click",
    "https://github.com/pallets/flask",
    "https://github.com/psf/requests",
    "https://github.com/encode/starlette",
    "https://github.com/miguelgrinberg/microblog",
    "https://github.com/kennethreitz/records",
    "https://github.com/encode/httpx",
    "https://github.com/python-attrs/attrs",
]

ROOT = Path(__file__).resolve().parent

#: Where the corpus is cloned. Outside the repository by default -- these are
#: ten full histories, and rescan_corpus.py needs the same working copies.
WORK = Path(os.environ.get("AG_CORPUS_DIR", Path(tempfile.gettempdir()) / "ag_corpus"))
OUT = ROOT / "results/first_scan.json"


def count_py(root: Path) -> tuple[int, int]:
    """(python files, total non-blank lines) excluding vendored/hidden dirs."""
    files = 0
    lines = 0
    for p in root.rglob("*.py"):
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if {"node_modules", "venv", ".venv", "build", "dist"} & set(rel.parts):
            continue
        files += 1
        try:
            lines += sum(1 for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip())
        except OSError:
            pass
    return files, lines


def clone(url: str, dest: Path) -> tuple[bool, int]:
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["git", "clone", "--quiet", url + ".git", str(dest)],
        capture_output=True, text=True, timeout=600, check=False,
    )
    if r.returncode != 0:
        return False, 0
    c = subprocess.run(["git", "-C", str(dest), "rev-list", "--count", "HEAD"],
                       capture_output=True, text=True, check=False)
    commits = int(c.stdout.strip() or 0) if c.returncode == 0 else 0
    return True, commits


async def analyse(repo_path: Path, url: str):
    from archguard.dashboard.pipeline_adapter import run_analysis_on_repo
    job = str(uuid.uuid4())
    t0 = time.monotonic()
    res = await run_analysis_on_repo(repo_path, job, url)
    return res, time.monotonic() - t0


def layer_rows(res) -> list[dict]:
    rows = []
    for lr in (getattr(res, "layer_results", None) or []):
        d = lr if isinstance(lr, dict) else getattr(lr, "__dict__", {})
        rows.append({
            "layer": d.get("layer"),
            "name": d.get("name"),
            "score": d.get("score"),
            "skipped": d.get("skipped"),
            "skip_reason": (d.get("skip_reason") or "")[:120],
            "violations": d.get("violation_count"),
        })
    return rows


async def main() -> None:
    results = []
    for url in REPOS:
        name = url.rstrip("/").split("/")[-1]
        dest = WORK / name
        print(f"=== {name}", flush=True)
        ok, commits = clone(url, dest)
        if not ok:
            results.append({"repo": name, "url": url, "status": "clone_failed"})
            print("   clone failed", flush=True)
            continue
        files, loc = count_py(dest)
        rec = {"repo": name, "url": url, "commits": commits,
               "py_files": files, "py_loc": loc}
        try:
            res, dur = await analyse(dest, url)
            rec.update({
                "status": "ok",
                "duration_s": round(dur, 2),
                "health": getattr(res, "health_score", None),
                "grade": getattr(res, "health_grade", None),
                "band": str(getattr(res, "band", "") or ""),
                "composite": getattr(res, "composite_score", None),
                "skipped": getattr(res, "skipped", None),
                "skip_reason": (getattr(res, "skip_reason", "") or "")[:160],
                "total_violations": getattr(res, "total_violations", None),
                "layers": layer_rows(res),
            })
            print(f"   ok health={rec['health']} {rec['grade']} in {rec['duration_s']}s", flush=True)
        except Exception as exc:
            rec.update({"status": "error", "error_type": type(exc).__name__,
                        "error": str(exc)[:200]})
            print(f"   ERROR {type(exc).__name__}: {str(exc)[:120]}", flush=True)
        results.append(rec)
        OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT}", flush=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
