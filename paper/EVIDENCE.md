# Evidence map

Every claim the paper makes, and what supports it. The purpose is to make the
unsupported claims as easy to find as the supported ones.

Most of the quantitative claims are checked mechanically rather than listed
here as prose, because a prose list goes stale the first time the corpus is
re-run and nothing complains:

```bash
python paper/check_claims.py
```

That re-derives each printed number from `results/*.json` and asserts the built
PDF still contains it. What follows covers what a script cannot judge.

## Claims resting on the measurements

| Claim | Where | Source |
|---|---|---|
| All ten repositories completed both scans without configuration | §VII-A | `results/first_scan.json`, `results/repeat_scan.json` — every record has `"status": "ok"` |
| First-scan active set is layers 2 and 4, for all ten | §VII-B | `active_1` in `repeat_scan.json`; asserted by `check_claims.py` |
| Repeat-scan active set is layers 2, 3 and 4, for all ten | §VII-B | `active_2`, same file, same assertion |
| Layer 1 never activated | §VII-B, §VIII-A | `layers[0].skipped` is true in every record |
| Layer 3's skip reason is the absence of a baseline | §VII-B | `skip_reason` on layer 3 of each first-scan record |
| Health rose on five of ten with no code change | §VII-C | `health_1` vs `health_2`; both scans ran on the same working copy |
| Four layer-2 violations, at fan-outs 11, 12, 15, 21 | §VIII-B | `layers[1].violations` and the violation messages in `first_scan.json` |
| Repeat scans are faster | §VII-D | `dur_1` vs `dur_2`; the caveat that this is one workstation is stated in §VI |

## Claims resting on the implementation, not on measurement

These describe what the code does. They are checkable by reading it, not by
running the corpus, and `check_claims.py` does not cover them.

| Claim | Where | Where to check |
|---|---|---|
| Contract inference declines to emit import rules, by design | §VIII-A | `archguard/analysis/_synthesis.py` — the comment states the reason |
| Layer 2's budget is a fixed profile threshold, not derived from the repository | §VIII-B | `coupling_budget: 10` under the `ci` profile |
| The drift threshold is 0.35 | §IV | `1 - min_cohesion`, `min_cohesion: 0.65` in the same profile |
| The composite averages over active layers only | §IV | `compute_archdebt` in `archguard/analysis/scoring.py` |
| Centroids persist per repository, so a second scan has a baseline | §V, §IX | `archguard/db/models.py::ModuleCentroid`, `tests/integration/test_centroid_persistence.py` |
| Duplication thresholds were fixed by design, not calibrated | §IX | the constants in the layer 4 runner; no calibration step exists |

## Claims about the system analysing itself

§V reports test counts, branch coverage and the release gate score. These are
self-consistency checks, and the paper says so in the same sentence. They are
also the only numbers in the paper that move when the repository changes
without the corpus being re-run, so they need a manual re-check before
submission:

```bash
pytest tests/unit tests/integration -m "not slow"   # pass / skip counts
python -m archguard.release_gate                    # health, file count, gates
```

## What is claimed to be unsupported

Stated as limitations rather than findings. Listing them here so that a reader
checking the evidence map does not have to infer them from §IX:

- No ground truth for module boundaries or architectural quality, so **no
  accuracy, precision or recall is reported** for any layer.
- Layer 3 was shown to **activate and compare**, not to detect: both scans
  observed identical code, so drift was near zero by construction.
- The language model component is **unevaluated**. No claim is made about the
  usefulness of its output.
- The self-analysis figure is **not independent evidence** — the system, the
  contract and the thresholds share an author.
- Ten Python repositories, nine of them libraries, do not generalise to other
  languages, to applications, or to repositories under active architectural
  change.

## Citations

Twenty-three references, every one cited in the text (`check_claims.py`'s
sibling check in the build confirms no gaps). Each supports a statement about
prior work or about a component the system uses; none is cited for a result
this paper claims.
