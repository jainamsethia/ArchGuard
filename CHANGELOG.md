# Changelog

## v1.0.0 — 2026-06-29

### Critical fixes
- CRIT-01: Added session-cookie authentication for browser clients. Web UI is now fully functional with ARCHGUARD_DASHBOARD_TOKEN set.
- CRIT-02: SSE stream endpoint now validates session cookies. EventSource connections from the browser no longer receive HTTP 401.

### Security hardening
- HIGH-01: Rate limiter now reads real client IP via X-Forwarded-For when ARCHGUARD_TRUSTED_PROXY_IPS is configured.
- HIGH-02: job_id query parameters now require UUID format (hex/hyphen, 36–64 chars); path traversal inputs return HTTP 422.
- MED-02: max_commits capped at 100 via Pydantic Field(le=100); values above 100 return HTTP 422.
- MED-03: Added 1 MB request body size limit middleware; oversized payloads return HTTP 413.

### Backend reliability
- MED-01: Analysis workspace directories are now deleted immediately after job completion (keep_alive=False).
- IMPROVEMENT-02: Periodic 15-minute background task removes any crash-orphaned workspace directories.

### Web flow
- LOW-01: Dependency graph panel now renders using vis-network (vendor asset was present but never loaded).
- IMPROVEMENT-03: Analysis submission page falls back to polling if SSE stream fails within 3 seconds.

### UI/UX
- LOW-02: Metric panels now show animated CSS skeleton shimmers while data loads.
- LOW-03: CSS class names replaced from machine-generated gen-style-N to semantic names; three duplicate button rules consolidated.
- LOW-04: Added 480px mobile breakpoint to dashboard and index pages; usable at 375px viewport.

### Deployment & CI
- LOW-05: CI test matrix now covers Python 3.11 and 3.12.
- LOW-06: Coverage threshold of 80% enforced; CI fails if coverage drops below this.
- LOW-07: bandit SAST scan added to CI security job.
- Removed dead httpx2 dev dependency from pyproject.toml.
- Added clarifying comment to Dockerfile HEALTHCHECK regarding Railway port behavior.

### Performance baseline
- octocat/Hello-World analysis: 3.04 seconds wall-clock on 2026-06-29.

## [0.5.0] — Unreleased

### Audit Report Fixes
- CRIT-01: Fixed `app.url_path_for` vs trailing slash issue breaking 404 handlers.
- CRIT-02: Removed broken redundant audit-log try block in `pipeline_adapter.py`.
- CRIT-03: Fixed CSP block in `dashboard.html` by adding nonce to SSE script.
- CRIT-04: Fixed HTTP 401 on `dashboard.html` jobs API by adding Token Basic Auth headers.
- HIGH-03: Fixed result card JS to read `health_score` from top-level `AnalysisJobResult`.
- MED-04: Fixed CSS mobile breakpoint selectors in both pages (`.input-group`, `.tablist`).
- MED-05: Added 'Inter' font to `dashboard.html` body to match `index.html`.
- LOW-02: Added `ARCHGUARD_SESSION_COOKIE_TTL`, `OPENAI_API_KEY`, etc., to `.env.example`.
- LOW-03: Linked `index.css` inside `dashboard.html` for semantic utility classes.
- LOW-04: Added inline comments distinguishing `SESSION_TTL` from `SESSION_COOKIE_TTL`.
- IMPROVEMENT-01: Added `dashboard-smoke.yml` GitHub Action for token-auth CI testing.
- IMPROVEMENT-03: Added `playwright.config.ts` for automated visual regression checks.

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
