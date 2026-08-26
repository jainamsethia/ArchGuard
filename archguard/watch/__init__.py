"""Watched repositories: rescan on a schedule, and say when something regressed.

Built on what was already here rather than beside it -- `alerting.trend_detector`
for the comparison, `alerting.webhooks` for delivery, `utils.url_validator` for
the SSRF guard, the arq worker for scheduling, and the same analysis pipeline a
manual scan uses. A watched rescan is an ordinary job; only what happens after
it lands is new.
"""
