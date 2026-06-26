# Changelog

## [0.4.0] — Unreleased

### Security (added by this guide)
- Fixed [CRIT-003] Implemented `ARCHGUARD_TRUSTED_PROXY_IPS` in `check_token` — proxy headers now correctly identify real client IP for token validation
- Fixed [CRIT-002] Removed hardcoded `ARCHGUARD_DASHBOARD_ALLOW_REMOTE=1` from docker-compose.yml default environment
- Fixed [CRIT-001] Tightened GitHub URL regex to reject path traversal suffixes; `git clone` now uses a safe reconstructed URL from validated owner/repo parts only
- Fixed [HIGH-001] Auth bypass for proxied requests resolved by CRIT-003 implementation
- Fixed [HIGH-003] Capped advisor session history at 20 turns to prevent token-cost DoS
- Fixed [HIGH-006] Removed `ARCHGUARD_DASHBOARD_ALLOW_REMOTE` from render.yaml default environment
- Fixed [MED-001] Replaced `<meta>` CSP tag with server-side `Content-Security-Policy` header; removed hardcoded `http://localhost:8000` from connect-src
- Fixed [MED-003] CSRF risk eliminated structurally by CRIT-002; added startup error when ALLOW_REMOTE=1 without token
- Fixed [MED-004] Added `max_length` validators to `AdvisorAskRequest`, `AdvisorMessageRequest`, and `RemediationRequest` Pydantic models
- Fixed [MED-005] Prompt injection: redact secrets from user messages before LLM submission; added injection-guard system preamble
- Fixed [MED-006] `POST /api/jobs` now validates that the repository exists via GitHub API before queuing a clone job

### Fixed (added by this guide)
- [HIGH-002] Moved `restart: unless-stopped` from inside docker-compose.yml healthcheck block to service level (was silently ignored)
- [HIGH-004] Replaced deprecated `asyncio.get_event_loop()` with `asyncio.get_running_loop()` in pipeline_adapter.py
- [HIGH-005] Added `HEALTHCHECK` instruction to Dockerfile using Python urllib (curl absent from runtime image)
- [MED-008] Replaced host `/tmp` bind-mount in docker-compose.yml with isolated `tmpfs` mount (prevents host temp-namespace exposure)
- [LOW-007] Fixed non-atomic semaphore initialization race in `JobManager._ensure_semaphore()`
- [LOW-001] Added `archguard/risk/__init__.py` for explicit package declaration
- [LOW-003] Narrowed five bare `except Exception` clauses to specific exception types with structured logging
- [LOW-006] Added `--slim` stub to `report_cmd.py` documenting planned CDN mode
- [LOW-008] Fixed health endpoint to use `importlib.metadata.version()` instead of `getattr(archguard, "__version__", "unknown")`

### Improved (added by this guide)
- [LOW-002] Documented `LocalLLMExplainer` as implemented-but-unwired pending CLI integration
- [LOW-004] Raised test coverage threshold from 70% to 80%; added tests for `archguard.risk.pr_risk` and `archguard.llm.local`
- [LOW-005] Documented API versioning policy in `app.py`; added `/api/v1/` aliases for advisor session routes
- [MED-002] Added startup warning documenting in-memory state constraint for multi-instance deployments

### Added (added by this guide)
- [ENH-003/MOD-003] Nonce-based CSP: per-request nonce generated in `_security_headers` middleware; `script-src 'unsafe-inline'` removed; `index.html` inline script extracted to `static/index.js`

### Test Infrastructure (NEW-BUG-00)
- Fixed stale import in `tests/integration/test_advisor_endpoint.py`: `archguard.dashboard._state` (removed in 0.4.0) → `archguard.dashboard._rate_limit`
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
