# Security Policy

## Supported Versions

Only the latest release receives security fixes.

## Reporting a Vulnerability

Report security vulnerabilities privately via GitHub's Security Advisories feature
(Security tab → "Report a vulnerability"). Do NOT open a public issue.

We aim to respond within 48 hours and provide a fix within 7 days for critical issues.

## Known Security Considerations

- **No user code is executed.** Python sources are read through `ast`, never
  imported. The analysis does shell out to `git` inside the clone and, for a
  dependency scan, runs `pip-audit` over a requirements file — both with
  argument lists rather than a shell, with explicit timeouts, and `pip-audit` is
  deliberately never pointed at a *project*, because resolving one would run the
  repository's own build backend.
- **Untrusted repositories are parsed in the worker, not the web process.** The
  process holding every session key does not clone or analyse anything.
- **LLM API keys** are read from the environment and never logged. Secrets are
  redacted from prompts on the way *out* to the model by
  `utils/content_filter.py` — that is the direction that matters, since the
  content being sent is the user's own source.
- **Repository URLs** are parsed with an anchored regex and the clone URL is
  rebuilt from the validated owner and repository name, so nothing a user typed
  is passed to `git`.
- **Outbound webhooks are SSRF-guarded.** `utils/url_validator.py` requires
  HTTPS, resolves the hostname once, refuses any address that is not globally
  routable — including carrier-grade NAT and the IPv4-in-IPv6 forms that
  ordinary range checks miss — and then connects to *that* address, so the
  address checked is the address contacted. Redirects are refused, and the
  reply is read up to a cap. The same guard runs when a webhook is configured
  and again on every send, because DNS can be repointed in between.
- **Audit log integrity**: with no `ARCHGUARD_AUDIT_SECRET` set, a random
  32-byte key is generated on first use and stored beside the log at `0600`.
  There is no hardcoded default. Supply the variable explicitly where entries
  must be verifiable elsewhere, or where the key should not sit on the same
  disk as the log; set `ARCHGUARD_AUDIT_STRICT=1` to refuse to log rather than
  generate one.
- **Sessions** are signed with `SESSION_SECRET`, stored in Redis with a TTL, and
  revoked server-side on sign-out. Rotating `SESSION_SECRET` invalidates every
  outstanding session; rotating `ARCHGUARD_DASHBOARD_TOKEN` does not touch them.
- **Tenancy**: every data query filters on the account. A repository watched by
  two people is two rows with two thresholds and two webhooks, and an id
  belonging to someone else answers 404 rather than 403, because 403 confirms
  the id exists.
