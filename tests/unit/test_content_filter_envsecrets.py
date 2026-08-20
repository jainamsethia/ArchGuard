"""Regression tests for MED-03: env-driven and fine-grained PAT redaction."""


def test_dashboard_token_exact_match_redacted(monkeypatch):
    """ARCHGUARD_DASHBOARD_TOKEN is redacted when it appears in text."""
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "my-secret-token-1234")
    from archguard.utils.content_filter import redact_secrets
    result = redact_secrets("the token is my-secret-token-1234 and nothing else")
    assert "my-secret-token-1234" not in result.text
    assert "ARCHGUARD_DASHBOARD_TOKEN" in result.redactions
    assert "[REDACTED:ARCHGUARD_DASHBOARD_TOKEN]" in result.text


def test_fine_grained_github_pat_redacted(monkeypatch):
    """Fine-grained GitHub PATs (github_pat_...) are redacted by regex."""
    monkeypatch.delenv("ARCHGUARD_DASHBOARD_TOKEN", raising=False)
    from archguard.utils.content_filter import redact_secrets
    result = redact_secrets("token github_pat_ABC123DEF456GHI789JKL0XYZ and done")
    assert "github_pat_" not in result.text
    assert "GITHUB_PAT_FINE" in result.redactions


def test_both_redactions_in_one_call(monkeypatch):
    """Both env-driven and regex redaction fire in a single call."""
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "my-secret-token-1234")
    from archguard.utils.content_filter import redact_secrets
    result = redact_secrets(
        "the token is my-secret-token-1234 and also github_pat_ABC123DEF456GHI789JKL0"
    )
    assert "my-secret-token-1234" not in result.text
    assert "github_pat_ABC123DEF" not in result.text
    assert "ARCHGUARD_DASHBOARD_TOKEN" in result.redactions
    assert "GITHUB_PAT_FINE" in result.redactions


def test_short_token_not_redacted(monkeypatch):
    """Tokens shorter than 8 chars are ignored (placeholder protection)."""
    monkeypatch.setenv("ARCHGUARD_DASHBOARD_TOKEN", "short")
    from archguard.utils.content_filter import redact_secrets
    result = redact_secrets("value is short here")
    assert "short" in result.text  # must NOT be redacted
    assert "ARCHGUARD_DASHBOARD_TOKEN" not in result.redactions
