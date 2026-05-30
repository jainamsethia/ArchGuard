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
