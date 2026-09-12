"""Second-scan condition: re-analyse each already-scanned working copy.

Run 1 (run_corpus.py) wrote a contract and an embedding baseline into each
clone. This pass measures what changes on a repeat scan of the same tree, so it
must see the same working copies -- set AG_CORPUS_DIR identically for both.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent

WORK = Path(os.environ.get("AG_CORPUS_DIR", Path(tempfile.gettempdir()) / "ag_corpus"))
FIRST = ROOT / "results/first_scan.json"
OUT = ROOT / "results/repeat_scan.json"


async def main() -> None:
    from archguard.dashboard.pipeline_adapter import run_analysis_on_repo

    first = {r["repo"]: r for r in json.load(FIRST.open(encoding="utf-8"))}
    out = []
    for name, prev in first.items():
        if prev.get("status") != "ok":
            continue
        p = WORK / name
        if not p.exists():
            continue
        t0 = time.monotonic()
        try:
            res = await run_analysis_on_repo(p, str(uuid.uuid4()), prev["url"])
        except Exception as exc:
            out.append({"repo": name, "status": "error", "error": str(exc)[:150]})
            print(f"{name}: ERROR {exc}", flush=True)
            continue
        dur = time.monotonic() - t0
        layers = []
        for lr in res.layer_results:
            d = lr if isinstance(lr, dict) else getattr(lr, "__dict__", {})
            layers.append({
                "layer": d.get("layer"), "score": d.get("score"),
                "skipped": d.get("skipped"),
                "skip_reason": (d.get("skip_reason") or "")[:110],
                "violations": d.get("violation_count"),
            })
        active = [x["layer"] for x in layers if not x["skipped"]]
        rec = {
            "repo": name, "status": "ok",
            "health_1": prev["health"], "health_2": res.health_score,
            "composite_1": prev["composite"], "composite_2": res.composite_score,
            "dur_1": prev["duration_s"], "dur_2": round(dur, 2),
            "active_1": [x["layer"] for x in prev["layers"] if not x["skipped"]],
            "active_2": active,
            "layers": layers,
        }
        out.append(rec)
        print(f"{name:<14} health {prev['health']:>5} -> {res.health_score:<5} "
              f"active {rec['active_1']} -> {active}  {prev['duration_s']}s -> {rec['dur_2']}s",
              flush=True)
        OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
