# ADR-009: Incremental re-analysis is already done, at the layer that matters

**Status:** Accepted

**Deciders:** ArchGuard Engineering

**Date:** 2026-08-24

## Context

The plan carried a task to implement incremental re-analysis, and the codebase
carried two mechanisms for it, neither wired to anything:

* `archguard/cache/incremental.py` — SHA-256 content hashes in a JSON file at
  the repository root. Imported only by tests. `tests/unit/test_retained_subsystems.py`
  keeps it alive on purpose, as "the basis for incremental re-analysis".
* The `file_hashes` table — the same idea keyed by repository so it can survive
  a throwaway clone. Present in the schema and in `db/models.py`; written and
  read by nothing outside a schema test.

The README also claimed a capability that did not exist:

> **Incremental analysis**: SHA-256 file hashing + SQLite WAL cache — only
> recomputes changed files

The second half of that is not what happens. `dashboard/pipeline_adapter.py`
says so in its own docstring: *"'changed_files' for a fresh clone = all .py
files in the repo, since every file is 'new' from the pipeline's perspective on
a fresh workspace."* Every file is read, parsed and chunked on every run.

Before building the missing half, we measured where the time actually goes.

## Measurements

ArchGuard analysing itself: 114 source files, 651 KiB, 562 functions.

Per-layer, cold process:

| Stage | Time |
|---|---|
| Layer 1 — import boundaries | 0.21s |
| Layer 2 — coupling | 0.47s |
| **Layer 3 — semantic drift** | **26.53s** |
| Layer 4 — duplication | 0.23s |
| Fitness functions | 0.72s |
| **Total** | **29.49s** |

Layer 3 is ~90% of a run, so it is the only stage worth optimising. Breaking it
down in a cold process:

| Component | Time |
|---|---|
| `import sentence_transformers` (pulls in torch) | 19.06s |
| `SentenceTransformer("all-MiniLM-L6-v2")` load | 7.30s |
| Chunk + hash all 562 functions | 1.71s |

The dominant cost is loading the library and the model — not embedding, and not
per-file work. Two caches already remove almost all of the rest:

* `EmbeddingCache` is keyed by `file_path::function_name` with the content hash
  compared on read, and `file_path` is repository-relative, so the key is stable
  across the throwaway clones. An unchanged function is never re-embedded.
* `_GLOBAL_MODEL_CACHE` in `analysis/semantic.py` is module-level, and the arq
  worker is a long-lived process. The model loads once per worker, not per job.

Running the orchestrator three times in one process — which is what the worker
actually does — gives the production figure:

| Run | Total | Layer 3 |
|---|---|---|
| 1 (cold process) | 29.49s | 26.53s |
| 2 (same process) | **4.57s** | **2.03s** |
| 3 (same process) | 3.97s | 2.19s |

## Decision

**Do not wire file-level hash gating into the pipeline.**

A repeat analysis of an unchanged repository already costs ~4s in a warm
worker. File-level gating could remove at most the ~1.7s of chunking plus
~0.07s of Layer 1 parsing, and only by adding cross-run state that has to be
kept consistent with the clone, the contract and the suppression set.

It is also not safe in the obvious form. The CLI could skip unchanged files
because it reported on a pull-request diff. The web application publishes a
whole-repository health report, and Layers 2 and 4 are graph- and
corpus-wide — a file omitted from the input is not a file whose findings stay
the same, it is a file whose findings disappear from the report. "Incremental"
here has to mean *cache the derived data*, which is what the embedding cache
already does, and never *drop files from the analysis*.

## Consequences

* The README claim is corrected to describe the cache that exists.
* `cache/incremental.py` and the `file_hashes` table stay, still guarded by
  `test_retained_subsystems.py`. They are not wired, and this ADR is the reason
  why, so the next reader does not re-derive it.
* For scheduled re-scans of watched repositories, where this cost does matter
  because it is multiplied by the number of watched repositories, the cheap
  correct gate is the **commit SHA**, not a file hash table. `runs` already
  stores `repository_id` and `commit_sha`; if HEAD has not moved since the last
  recorded run, nothing can have changed, and the previous result can be reused
  without hashing a single file. That check needs no new schema.
* If a repository ever appears where per-file work is genuinely the bottleneck
  — very large, or Layer 3 disabled so the ML cost is absent — this decision
  should be revisited with a measurement on that repository rather than in the
  abstract.

## Options considered

### 1. Hash every file and pass only changed files to the pipeline

**Rejected.** Saves ~1.7s of ~4s, and under-reports: Layers 2 and 4 need the
whole corpus, so omitting unchanged files removes their findings from a report
that is supposed to describe the whole repository.

### 2. Hash every file and cache per-file derived data (parsed imports)

**Rejected.** Correct, but the thing it would avoid is 0.21s of Layer 1 and
1.71s of chunking. Persisting and invalidating that state costs more than it
saves.

### 3. Gate on commit SHA for scheduled re-scans

**Deferred to the watched-repositories work**, where a scheduled scan of an
unchanged repository is the case that actually recurs. Recorded here because it
is the design that replaces the one this ADR rejects.
