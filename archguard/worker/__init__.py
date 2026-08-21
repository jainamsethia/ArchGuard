"""The analysis worker: the process that clones and analyses repositories.

Separated from the web process so a restart does not cancel every running
analysis, so a second web instance can see the first one's jobs, so the web
image need not carry torch, and so untrusted repositories are parsed somewhere
that holds no session keys.
"""
