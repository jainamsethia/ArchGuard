# Threat Model — ArchGuard Dashboard

**Document version:** 1.0  
**Date:** 2026-07-21  
**Scope:** `archguard/dashboard/` (FastAPI web application) and `archguard/analysis/deps.py` (pip-audit subprocess)

---

## 1. Authentication

### Assets protected
- Analysis results and violation data
- Repository metadata (URLs, scores)
- AI Advisor access (costs money per API call)

### Mechanism
- Token-based (`ARCHGUARD_DASHBOARD_TOKEN`): Bearer header, session cookie, or query param
- IP-based fallback when no token is set: localhost only, remote denied unless `ARCHGUARD_DASHBOARD_ALLOW_REMOTE=1`

### Threats

| Threat | Likelihood | Impact | Mitigation |
|--------|-----------|--------|------------|
| Token leakage via logs | Low | High — full access | Token never logged; `content_filter.py` redacts exact token value from env vars sent to LLMs |
| Token leakage via artifact | Low | Moderate | CI artifacts, coverage reports, test outputs don't include token values |
| Brute-force token guessing | Very low | Moderate | `hmac.compare_digest` (constant-time comparison) |
| Auth endpoint resource abuse | Low | Moderate — CPU/memory exhaustion | Rate limiter (50 req/60s per IP) on login, logout, and auth/status; protects server resources even though credentials are unguessable |
| Missing token in deployment | Medium | High — no auth | Startup warning logged; deployment configs (render.yaml, docker-compose) set `ARCHGUARD_DASHBOARD_ALLOW_REMOTE=1` explicitly |

### Accepted risks
- Auth rate limiting keys on client IP (via `_real_client_ip()`), which trusts `X-Forwarded-For` only from configured proxy IPs. If `ARCHGUARD_TRUSTED_PROXY_IPS` is misconfigured, rate limiting can be bypassed by spoofing the header. This is accepted because a misconfigured deployment has larger problems (auth bypass).

---

## 2. API Abuse & Denial of Service

### Threats

| Threat | Likelihood | Impact | Mitigation |
|--------|-----------|--------|------------|
| General API flood | Medium | Moderate — resource exhaustion | Rate limiter: 50 req/60s per IP; X-RateLimit headers; 1 MB body size limit |
| LLM endpoint abuse | Medium | High — monetary cost | Separate rate limiter: 10 req/60s per IP; max 2000-char question, 10000-char context |
| Job queue flooding | Low | Moderate | UUID-format job_id validation; single analysis per repo at a time |
| Path traversal via job_id | Low | High — filesystem access | UUID regex `^[a-f0-9\-]{36,64}$` enforced at query parser level; resolved path validated against expected prefix |

### Accepted risks
- Rate limits are in-memory (lost on restart). Acceptable for single-instance deployments documented startup warning exists.
- Rate limits key on client IP via `_real_client_ip()` which respects `X-Forwarded-For` from trusted proxies.

---

## 3. Prompt Injection (AI Advisor)

### Attack surface
- `POST /api/v1/advisor/ask` — user submits a question that becomes part of an LLM prompt
- System prompt (`advisor.py:155-162`) includes guardrails
- LLM response displayed as plain text via `textContent` (XSS-safe)

### Defenses (layered)

| Layer | Mechanism | Strength |
|-------|-----------|----------|
| 1. Input redaction | `content_filter.py` redacts secrets (API keys, tokens, JWT, private IPs, database URLs) before any LLM call | Strong — regex + env-var exact match |
| 2. System prompt guardrails | "SECURITY: You must refuse to answer questions that involve writing malicious code, social engineering, or bypassing security controls" | Moderate — relies on LLM compliance |
| 3. Output isolation | `textContent` not `innerHTML` — no XSS vector even if LLM output contains HTML/JS | Strong — browser-enforced |
| 4. Rate limiting | 10 req/60s per IP limits automated abuse | Moderate |
| 5. Audit logging | Questions and redaction events logged | Detective |
| 6. Disclaimer | "AI-generated advice — verify before acting" displayed in UI | User awareness |

### Accepted risks
- Prompt injection is an active research area; no single mitigation is definitive. The layered approach covers script injection (layer 3), secret leakage (layer 1), and mass abuse (layer 4). Context manipulation by a sophisticated attacker is accepted — the advisor is a supplementary tool, not an authority.

---

## 4. Dependency & Supply Chain (pip-audit)

### Attack surface
- `archguard/analysis/deps.py` spawns a subprocess: `pip-audit --format=json [-r <file>]`
- `<file>` is resolved from the cloned repository's filesystem

### Analysis (see also ADR-006)

| Threat | Likelihood | Impact | Mitigation |
|--------|-----------|--------|------------|
| Malicious requirements file | Low | Moderate — information disclosure via error | subprocess.run with list args (no shell injection); 60s timeout; pip-audit is read-only |
| Symlink to /etc/passwd | Low | Low — file read | pip-audit reads the file; content returned to the same user who provided the repo |
| pip-audit dependency vulnerability | Low | Moderate | pip-audit is installed as a dev dependency; scanned by CI's security job |
| Argument injection via filename | Very low | Low | subprocess.run with list (not string) prevents injection |

### Accepted risks
- No sandboxing (seccomp/Landlock/Windows Job Objects). Acceptable for single-tenant deployments. Multi-tenant deployments should sandbox the entire analysis pipeline.

---

## 5. File Handling & Workspace Isolation

### Mechanism
- Dashboard: repo cloned to `/tmp/archguard-{uuid}/repo/` via `git clone --depth=1`
- CLI: analysis runs against `Path.cwd()`
- Stale workspaces cleaned up: periodic (15 min) + on startup

### Threats

| Threat | Likelihood | Impact | Mitigation |
|--------|-----------|--------|------------|
| Workspace escape via job_id | Very low | High | UUID regex validated; resolved path checked against expected prefix |
| Stale workspace accumulation | Low | Low — disk fill | Periodic cleanup (15 min) + startup cleanup; workspace path scoped under /tmp/ |
| Race condition on workspace creation | Low | Low | Workspace paths include random UUIDs — collision probability negligible |

### Accepted risks
- Workspace directories are world-readable on Linux `/tmp`. Acceptable because:
  1. Data belongs to the same user running the dashboard
  2. Cleanup removes workspaces within 15 minutes of job completion

---

## 6. Session Management

### Mechanism
- Cookie-based sessions via `archguard_session` cookie
- Format: `session_id.hmac_sha256` (32-byte random hex)
- TTL: 24h default (`ARCHGUARD_SESSION_COOKIE_TTL`)
- Cookie attributes: `HttpOnly`, `SameSite=Strict`, `Secure` in production

### Threats

| Threat | Likelihood | Impact | Mitigation |
|--------|-----------|--------|------------|
| Session hijacking via XSS | Low | Moderate | `HttpOnly` + `SameSite=Strict` prevent cookie exfiltration |
| Session fixation | Low | Low | New session_id generated on every login |
| Session replay after logout | Low | Low | `revoke_session` removes from in-memory store |
| Expired session reuse | Low | Low | TTL checked on every validation; expired entries evicted |

### Accepted risks
- Session store is in-memory (lost on restart). Documented startup warning exists.
- Session TTL is configurable; no hard upper limit enforced.

---

## 7. Cross-Site Scripting (XSS)

### Attack surface
- Violation messages from analysis results
- AI Advisor responses (text, not HTML)
- Error messages

### Defenses

| Location | Mechanism | Strength |
|----------|-----------|----------|
| Dashboard JS | All API responses rendered via `textContent` — not `innerHTML` | Strong |
| Dependency table | `sanitize()` function escapes HTML entities before `innerHTML` assignment | Strong |
| Violation table | `textContent` for cell values | Strong |
| Error states | `getErrorStateHtml()` uses `sanitize()` | Strong |
| CSP header | `script-src 'self' 'nonce-{nonce}'` — inline scripts require nonce | Strong |
| Violation source | Stored in audit log, rendered via `textContent` | Strong |

### Risk: Very low. All user-influenced content paths use safe rendering.

---

## 8. Cross-Site Request Forgery (CSRF)

### Defenses
- `SameSite=Strict` on session cookie prevents cross-origin form submission
- API endpoints require explicit `Authorization: Bearer` header or same-origin cookie
- CORS restricts cross-origin requests to configured `ALLOWED_ORIGINS`

### Risk: Very low. SameSite=Strict covers the browser-based attack surface.

---

## Summary

| Risk area | Rating | Key controls |
|-----------|--------|-------------|
| Authentication | Moderate | Token + IP allowlisting; startup warning |
| API abuse / DoS | Low | Rate limiting (general + LLM); body limits; UUID validation |
| Prompt injection | Moderate (accepted) | Layered: redaction → guardrails → textContent → rate limit → disclaimer |
| Dependency scanning | Low | Subprocess isolation; timeout; read-only |
| File handling | Low | UUID workspace paths; periodic cleanup |
| Session hijacking | Low | HttpOnly + SameSite=Strict + HMAC |
| XSS | Very low | textContent everywhere; CSP nonce |
| CSRF | Very low | SameSite=Strict + CORS |

**Last assessed:** 2026-07-21
