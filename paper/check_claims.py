"""Check every quantitative claim the paper's prose makes against the raw results.

The tables are safe by construction -- `build_paper.py` formats them from
`results/*.json`, so they cannot drift. The prose is not: sentences like "the
first scan averaged 13.1 seconds" are typed, and a re-run of the corpus would
leave them quietly wrong while the tables beside them updated.

This script re-derives each of those numbers from the same JSON and asserts the
paper still says them. It reads the built PDF, so it checks what a reviewer
would actually read rather than what the source intended.

    python paper/check_claims.py

Exits non-zero and names every mismatch. Claims that cannot be derived from the
corpus results -- the self-analysis figures, which come from running the test
suite and the release gate -- are listed at the end as needing a manual re-check,
because pretending to verify them here would be worse than saying so.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent

first = json.loads((ROOT / "results/first_scan.json").read_text(encoding="utf-8"))
rescan = json.loads((ROOT / "results/repeat_scan.json").read_text(encoding="utf-8"))
OK = [r for r in first if r.get("status") == "ok"]
BY = {r["repo"]: r for r in rescan}


def paper_text() -> str:
    import pymupdf
    pdf = ROOT / "ArchGuard_IEEE_paper.pdf"
    if not pdf.is_file():
        sys.exit(f"{pdf} not found -- run build_paper.py then render_pdf.py first")
    with pymupdf.open(pdf) as doc:
        return " ".join(p.get_text() for p in doc).replace("\n", " ")


# --------------------------------------------------------------- derivations
# Each entry: (what the paper says, the value re-derived from the raw results).

claims: list[tuple[str, str, object]] = []


def claim(label: str, printed: str, derived: object) -> None:
    claims.append((label, printed, derived))


claim("corpus size (files)", f"{sum(r['py_files'] for r in OK)}",
      sum(r["py_files"] for r in OK))
claim("corpus size (lines)", f"{sum(r['py_loc'] for r in OK)}",
      sum(r["py_loc"] for r in OK))

# Durations exclude the first repository analysed: it absorbs the one-off
# embedding model load, which the paper states and Fig. 3 annotates.
tail = [r["repo"] for r in OK][1:]
d1 = [BY[n]["dur_1"] for n in tail]
d2 = [BY[n]["dur_2"] for n in tail]
claim("mean first-scan duration", f"{statistics.mean(d1):.1f}", round(statistics.mean(d1), 1))
claim("mean repeat-scan duration", f"{statistics.mean(d2):.1f}", round(statistics.mean(d2), 1))

reductions = [(a - b) / a for a, b in zip(d1, d2, strict=True)]
claim("mean duration reduction (pct)",
      f"{statistics.mean(reductions) * 100:.0f}", round(statistics.mean(reductions) * 100))
claim("min duration reduction (pct)", f"{min(reductions) * 100:.0f}", round(min(reductions) * 100))
claim("max duration reduction (pct)", f"{max(reductions) * 100:.0f}", round(max(reductions) * 100))

largest = max(OK, key=lambda r: r["py_loc"])
claim("largest repository (lines)", f"{largest['py_loc']}", largest["py_loc"])
claim("largest repository first-scan time", f"{largest['duration_s']:.1f}",
      round(largest["duration_s"], 1))

h1 = [BY[r["repo"]]["health_1"] for r in OK]
h2 = [BY[r["repo"]]["health_2"] for r in OK]
claim("mean health, first scan", f"{statistics.mean(h1):.1f}", round(statistics.mean(h1), 1))
claim("mean health, repeat scan", f"{statistics.mean(h2):.1f}", round(statistics.mean(h2), 1))
# Population, not sample: the ten repositories are the whole set measured, and
# the paper states outright that the corpus is too small for inferential
# statistics and reports none. Sample stdev would imply an inference to a
# population the paper explicitly declines to make (it gives 16.3 and 10.8).
claim("health stdev, first scan", f"{statistics.pstdev(h1):.1f}", round(statistics.pstdev(h1), 1))
claim("health stdev, repeat scan", f"{statistics.pstdev(h2):.1f}", round(statistics.pstdev(h2), 1))

improved = sum(1 for a, b in zip(h1, h2, strict=True) if b > a)
claim("repositories scoring higher on repeat", f"{improved}", improved)

# Layer activation: the paper's central result.
act1 = {tuple(BY[r["repo"]]["active_1"]) for r in OK}
act2 = {tuple(BY[r["repo"]]["active_2"]) for r in OK}


def layer(rec: dict, n: int) -> dict:
    return next(x for x in rec["layers"] if x["layer"] == n)


l2_hits = sum(1 for r in OK if layer(r, 2)["violations"])
l4_hits = sum(1 for r in OK if layer(r, 4)["violations"])
claim("repositories with a layer 2 violation", f"{l2_hits}", l2_hits)
claim("repositories with a layer 4 violation", f"{l4_hits}", l4_hits)

l4_scores = [layer(r, 4)["score"] for r in OK if layer(r, 4)["score"]]
if l4_scores:
    claim("the single layer 4 score", f"{l4_scores[0]:.3f}", round(l4_scores[0], 3))

worst = min(OK, key=lambda r: r["health"])
claim("lowest first-scan composite", f"{worst['composite']:.3f}", round(worst["composite"], 3))
claim("lowest first-scan health", f"{worst['health']:.1f}", round(worst["health"], 1))
claim("that repository's repeat composite", f"{BY[worst['repo']]['composite_2']:.3f}",
      round(BY[worst["repo"]]["composite_2"], 3))
claim("that repository's repeat health", f"{BY[worst['repo']]['health_2']:.1f}",
      round(BY[worst["repo"]]["health_2"], 1))

# ------------------------------------------------------------------- checking
text = paper_text()
failures: list[str] = []

for label, printed, _derived in claims:
    if printed not in text:
        failures.append(f"  {label}: the results give {printed!r}, which the paper does not contain")

if act1 != {(2, 4)}:
    failures.append(f"  first-scan active set is not uniformly (2, 4): {sorted(act1)}")
if act2 != {(2, 3, 4)}:
    failures.append(f"  repeat-scan active set is not uniformly (2, 3, 4): {sorted(act2)}")
if not all(layer(r, 1)["skipped"] for r in OK):
    failures.append("  layer 1 was not skipped on every repository")

# Every reference cited, every citation resolvable. A reference nobody cites is
# padding; a citation with no reference is a dangling pointer, and both are the
# kind of thing a reviewer finds before the authors do. A number is "cited" when
# it appears more than once: the reference list entry itself is one occurrence.
listed = {int(n) for n in re.findall(r"\[(\d+)\]\s+[A-Z]", text)}
occurrences = Counter(int(n) for n in re.findall(r"\[(\d+)\]", text))
if listed and (gap := set(range(1, max(listed) + 1)) - listed):
    failures.append(f"  reference numbers with no entry: {sorted(gap)}")
if (uncited := {n for n in listed if occurrences[n] < 2}):
    failures.append(f"  references never cited in the text: {sorted(uncited)}")

print(f"checked {len(claims)} derived values plus 3 structural invariants")
if failures:
    print("\nMISMATCH:")
    print("\n".join(failures))
    sys.exit(1)

print("all corpus-derived claims in the paper match results/*.json")
print("\nNot checkable here -- re-measure against the repository at the commit")
print("the paper describes, and update Section V if they have moved:")
print("  - the passing/skipping test counts   (pytest tests/unit tests/integration)")
print("  - branch coverage without ML extras  (pytest --cov-branch)")
print("  - release gate health and file count (python -m archguard.release_gate)")
