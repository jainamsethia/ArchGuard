# Changelog

## [0.4.0] — Unreleased
### Fixed
- CRITICAL: `cachetools` moved to main dependencies (was in optional extras, causing ModuleNotFoundError)
- CRITICAL: Dashboard now shows analysis results after GitHub URL submission (audit log was not written from web path)
- HIGH: Evolution UI tests now inspect `dashboard.html` instead of `index.html` (post-Phase 3 redesign)
- HIGH: GitHub rate limit (HTTP 429) no longer incorrectly returned as HTTP 404
- HIGH: SSE done event now passes `?job_id=` to dashboard redirect
- MEDIUM: SSE onerror handler no longer reconnects indefinitely on job failure
- MEDIUM: Dashboard no longer blanks entirely when a single API endpoint fails
- MEDIUM: `faiss.IndexFlatL2` now guarded against `faiss is None` (no AttributeError when faiss-cpu not installed)
- LOW: Duplicate `chart.js` asset removed (208 KB savings)
- LOW: CLI `--port` default aligned to 8000 (was 8080, now matches docker compose)
- LOW: Google Fonts CDN replaced with bundled Inter font (offline support)
- LOW: `asyncio.Semaphore` now created lazily in running event loop
- LOW: 4 stale `# type: ignore` comments removed (mypy CI now passes)

### Improved
- Empty state added to `dashboard.html` for first-time users (with CTA)
- Inline health score card rendered in `index.html` before dashboard redirect
- `vis-network.min.js` (644 KB) now lazy-loaded on Dependency Graph tab open
- SSE poll interval reduced from 500ms to 200ms for snappier progress updates
- 18 bare `except Exception` clauses reduced to ≤5 with specific exception types
- `_state.py` refactored into focused modules (_auth, _rate_limit, _sessions)
- Test coverage increased from 71.69% to ≥73%
- CORS `allow_headers=["*"]` tightened to explicit allowlist
- Smoke test script added (`scripts/smoke_test.sh`)
