# ADR-007: Thread pool sizing

**Status:** Accepted

**Deciders:** ArchGuard Engineering

**Date:** 2026-07-21

## Context

ArchGuard uses `concurrent.futures.ThreadPoolExecutor` in four distinct
places, each with its own `max_workers` value:

| Location | `max_workers` | Purpose |
|----------|---------------|---------|
| `analysis/_orchestrator_stages.py:34` | `2` | Run Layer 1 (boundary) and Layer 2 (coupling) analysis concurrently |
| `cli/history_analyze_cmd.py:301` | Configurable (`workers` CLI flag, default from config) | Run historical analysis across multiple commits in parallel |
| `evolution/tracker.py:143` | `4` (default in `max_workers` param) | Process evolution snapshots across commit history |
| `utils/async_utils.py:25` | `1` | Convert a blocking call into an awaitable (no parallelism intended) |

A previous version of `_orchestrator_stages.py` used `max_workers=4`; this
was reduced to `2` by commit `b765f59` during a security review that noted
resource contention between the I/O-heavy Layer 2 coupling analysis and
Layer 1 boundary analysis sharing the same GIL-bound process.

## Options considered

### 1. Shared global thread pool

Use a single module-level `ThreadPoolExecutor` across all callers.

**Rejected** because:
- Thread pools are resource domains: mixing a CPU-bound analysis stage with
  I/O-bound file reading in one pool creates starvation profiles that are hard
  to diagnose.
- `history_analyze_cmd` and `evolution/tracker` run as CLI commands with
  process-level lifetime; a global pool would keep threads alive after the
  CLI exits, delaying termination.
- Thread-local state and `rich.progress` task tracking are simpler when each
  pool is scoped to one call site.

### 2. `ProcessPoolExecutor` for CPU-bound stages

**Rejected** because:
- The analysis stages share in-memory objects (`AnalysisOrchestrator`,
  `Console`, progress trackers, metric collectors) that cannot be pickled.
- The overhead of serialising the file list and module graph for each
  parallel branch exceeds the GIL-release benefit for Layer 1 + Layer 2,
  which are largely I/O-bound (file reads).

### 3. Per-call-site pools (chosen)

Each call site owns its `ThreadPoolExecutor` as a context manager
(`with ThreadPoolExecutor(...) as executor:`), giving deterministic
lifetime and natural parallelism boundaries.

## Decision

### Rule for future thread pool usage

1. **Default to `max_workers=1`** when wrapping a synchronous function only
   for the purpose of making it awaitable (`async_utils.py` pattern).
2. **CPU-bound parallelism within analysis:** cap at `2` to avoid GIL
   contention on typical dual-core CI runners and developer machines.
3. **Batch/historical processing:** use a configurable `workers` parameter
   with a default of `4`, since these workloads are embarrassingly parallel
   across independent git commits and the primary bottleneck is subprocess
   spawn + git checkout, not the Python GIL.
4. **Never use `max_workers=None`** (which defaults to `min(32, cpu_count+4)`)
   for any analysis path without explicit review, because:
   - No analysis stage is purely I/O-bound (file reads are interleaved with
     parsing and AST traversal).
   - Oversubscribing CPU cores slows every stage due to GIL contention.
   - Oversubscribing on memory-constrained CI runners (e.g., GitHub Actions
     2-core/7 GB) causes OOM kills on layer 3 (Semantic Drift) which loads
     the entire module graph into memory.

## Consequences

- `history_analyze_cmd` and `evolution/tracker` can be tuned per-deployment
  via their `workers`/`max_workers` parameters without code changes.
- Layer 1 + Layer 2 run sequentially-ish on single-core CI runners, but the
  analysis pipeline is dominated by Layer 3 (semantic drift) anyway — the
  concurrency of L1+L2 is a minor gain, not a bottleneck.
- `ThreadPoolExecutor(max_workers=2)` in `_orchestrator_stages.py` is a
  `ponytail`-level simplification: 2 is the right number for today's
  workload, but if a future Layer is added that is purely I/O-bound (e.g.,
  fetching external API data per module), the pool size should be revisited
  with `max_workers=min(4, cpu_count)`.
