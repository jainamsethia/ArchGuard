# Security Policy

## Supported Versions

Only the latest release receives security fixes.

## Reporting a Vulnerability

Report security vulnerabilities privately via GitHub's Security Advisories feature
(Security tab → "Report a vulnerability"). Do NOT open a public issue.

We aim to respond within 48 hours and provide a fix within 7 days for critical issues.

## Known Security Considerations

- ArchGuard executes no user code. It only reads Python source files via the `ast`
module.
- LLM API keys are read from environment variables and never logged.
- The content filter (`utils/content_filter.py`) sanitizes LLM output before display.
- File paths are validated to prevent traversal attacks (see `--root` validation).
- **Audit Log Integrity**: By default, the audit log HMAC uses a generic hardcoded key. This provides no real integrity protection against malicious actors. For production use, you **must** supply a cryptographic key via the `ARCHGUARD_AUDIT_SECRET` environment variable. To strictly enforce this, set `ARCHGUARD_AUDIT_STRICT=1`, which will cause ArchGuard to throw a `ConfigError` and crash if the default secret is used.
