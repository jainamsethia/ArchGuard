# ADR-005: Deferred process isolation for hung analysis

**Status:** Accepted (deferred)

**Deciders:** ArchGuard Engineering

**Date:** 2026-07-21

## Context

`AnalysisOrchestrator.run()` (in `archguard/analysis/layers.py`) is a synchronous,
CPU-bound operation that performs up to 4 layers of static analysis over a set
of Python source files. It runs in-process and can block for minutes on large
repositories (see `tests/benchmarks/test_latency.py::test_analyze_warm_cache`,
which measures ~80 s on developer hardware).

The dashboard wraps this call via `run_in_executor(None, ...)` in
`pipeline_adapter.py` with an `asyncio.wait_for()` timeout (default 600 s,
configurable via `ARCHGUARD_ANALYSIS_TIMEOUT`). When the timeout fires,
`wait_for` raises `TimeoutError` in the async caller and the job is reported
as failed — **but** the thread pool worker continues executing the analysis in
the background until completion. The result is discarded.

This means:
- A genuinely hung analysis (deadlock, infinite loop, filesystem hang) occupies
  a thread pool slot until the OS scheduler yields it, even after the timeout.
- Under repeated timeouts, the default `ThreadPoolExecutor` will grow to its
  default max of `min(32, os.cpu_count() + 4)` workers, each running a hung
  analysis, exhausting system resources.
- No mechanism exists to send SIGKILL (or equivalent on Windows) to the
  offending computation.

## Options considered

### 1. Subprocess-based isolation (rejected for now)

Run each analysis in a child process wrapped by `subprocess.run()` with a
`timeout=` parameter. On timeout, `subprocess.TimeoutExpired` is raised and the
child process is killed by the Python runtime.

**Pros:**
- Guarantees resource cleanup on timeout — no zombie threads.
- Standard library, no external dependencies.
- Clean process boundary isolates memory corruption and leaks.

**Cons:**
- `AnalysisOrchestrator` is a complex in-memory object graph with file-based
  caching and module-level singletons. Serially it passes mutable state via
  orchestrator instances; serialising this to a subprocess CLI invocation
  would require a dedicated `--ci` / `--json` mode on the `analyze` command
  with a defined IPC contract.
- Significant refactoring effort (estimated 2–3 days) with risk of regression
  across 4 analysis layers.
- Would double memory footprint during analysis (parent + child).

### 2. Thread-level cancellation via `concurrent.futures` (not possible)

`Future.cancel()` in `concurrent.futures` cannot cancel a running task — it
only prevents *unstarted* tasks from running. The `_thread.interrupt_main()`
approach is fragile, CPython-specific, and does not work on Windows.

### 3. Daemon thread pool (rejected)

Running analysis in daemon threads would let the process exit while analysis
threads are still running. This trades one class of bug (leaked threads) for
another (partial results, corrupted caches, file handles left open).

### 4. Accept current behaviour (chosen)

The `asyncio.wait_for` timeout provides an application-level circuit breaker:
- The caller sees a clean error and can retry or notify the user.
- The leak is bounded: a thread pool slot is occupied until the analysis
  finishes, but the analysis *does* finish — it's never truly deadlocked;
  it's just slow (I/O-bound on file access, CPU-bound on parsing).
- The thread pool is private to each analysis call (created per-job in the
  dashboard), so a hung analysis only starves its own job.
- In practice, the `ARCHGUARD_ANALYSIS_TIMEOUT` default of 600 s is generous
  enough that slow repos complete before the timeout fires.

## Decision

**Defer** implementing process isolation until either:
1. A verified incident of a genuinely *deadlocked* (not merely slow) analysis
   is reported, with a reproduction case.
2. Memory profiling shows that in-process analysis causes heap pressure that
   a process boundary would contain.

Until then, the `asyncio.wait_for` timeout is sufficient as a first line of
defence, and the `run_analysis_on_repo` caller handles the `TimeoutError`
gracefully by logging the event and returning a structured `AnalysisJobResult`
with an `error` field.

## Consequences

- Thread pool slots may be wasted on timed-out-but-still-running analyses,
  reducing dashboard throughput under load. Mitigation: the dashboard processes
  one job at a time per workspace; queue depth is 1.
- If a future incident meets the bar above, migration to subprocess isolation
  will require `AnalysisOrchestrator` to expose a serialisable invocation path
  (e.g. `archguard analyze --ci --json`). That work has not been started.
- The `AsyncUtils.run_in_background` helper should not be used for tasks that
  wrap long-running synchronous code that could hang, for the same reason.
