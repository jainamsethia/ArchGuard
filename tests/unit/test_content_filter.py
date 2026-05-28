"""Unit tests for archguard.utils.content_filter."""

from __future__ import annotations

import string

from archguard.utils.content_filter import redact_secrets, is_safe_for_llm


class TestRedactSecrets:
    """Tests for redact_secrets."""

    def test_anthropic_key_redacted(self) -> None:
        """sk-ant- + 93 chars -> redacted as ANTHROPIC_KEY."""
        key = "sk-ant-" + "a" * 93
        text = f"my key is {key} here"
        result = redact_secrets(text)
        assert "[REDACTED:ANTHROPIC_KEY]" in result.text
        assert "ANTHROPIC_KEY" in result.redactions
        assert key not in result.text

    def test_git_sha_not_redacted(self) -> None:
        """40-char hex git SHA must NOT match OPENAI_KEY pattern."""
        sha = "a" * 40  # 40 hex chars — not 48 alnum
        text = f"commit {sha} deployed"
        result = redact_secrets(text)
        assert sha in result.text
        assert "OPENAI_KEY" not in result.redactions

    def test_jwt_redacted(self) -> None:
        """Three-part eyJ... token -> redacted as JWT."""
        # Build a realistic JWT structure with 3 segments
        seg1 = "eyJ" + "a" * 25
        seg2 = "b" * 25
        seg3 = "c" * 25
        jwt = f"{seg1}.{seg2}.{seg3}"
        text = f"token={jwt}"
        result = redact_secrets(text)
        assert "[REDACTED:JWT]" in result.text
        assert "JWT" in result.redactions

    def test_aws_key_redacted(self) -> None:
        """AKIA + 16 uppercase alphanumeric -> redacted as AWS_KEY."""
        key = "AKIA" + "A" * 16
        text = f"aws_access_key={key}"
        result = redact_secrets(text)
        assert "[REDACTED:AWS_KEY]" in result.text
        assert "AWS_KEY" in result.redactions

    def test_github_pat_redacted(self) -> None:
        """ghp_ + 36 alphanumeric -> redacted as GITHUB_PAT."""
        pat = "ghp_" + "x" * 36
        text = f"token: {pat}"
        result = redact_secrets(text)
        assert "[REDACTED:GITHUB_PAT]" in result.text
        assert "GITHUB_PAT" in result.redactions

    def test_credential_pattern_redacted(self) -> None:
        """password = 'mysecretpass' -> redacted as CREDENTIAL."""
        text = 'password = "mysecretpassword"'
        result = redact_secrets(text)
        assert "[REDACTED:CREDENTIAL]" in result.text
        assert "CREDENTIAL" in result.redactions

    def test_private_ip_192_168(self) -> None:
        """192.168.1.1 -> redacted as PRIVATE_IP."""
        text = "connect to 192.168.1.1 on port 8080"
        result = redact_secrets(text)
        assert "[REDACTED:PRIVATE_IP]" in result.text
        assert "PRIVATE_IP" in result.redactions

    def test_private_ip_10(self) -> None:
        """10.0.0.1 -> redacted as PRIVATE_IP."""
        text = "server at 10.0.0.1"
        result = redact_secrets(text)
        assert "[REDACTED:PRIVATE_IP]" in result.text
        assert "PRIVATE_IP" in result.redactions

    def test_clean_text_unchanged(self) -> None:
        """No secrets -> no redactions, text unchanged."""
        text = "This is a clean violation message about imports."
        result = redact_secrets(text)
        assert result.text == text
        assert result.redactions == []

    def test_multiple_secrets_all_redacted(self) -> None:
        """Multiple secrets -> all redacted, labels list has all types."""
        pat = "ghp_" + "x" * 36
        text = f"keys: {pat} and server 192.168.1.100"
        result = redact_secrets(text)
        assert "[REDACTED:GITHUB_PAT]" in result.text
        assert "[REDACTED:PRIVATE_IP]" in result.text
        assert "GITHUB_PAT" in result.redactions
        assert "PRIVATE_IP" in result.redactions

    def test_redaction_idempotent(self) -> None:
        """Applying redact_secrets twice gives same result."""
        pat = "ghp_" + "x" * 36
        text = f"token: {pat}"
        first = redact_secrets(text)
        second = redact_secrets(first.text)
        assert first.text == second.text


class TestIsSafeForLLM:
    """Tests for is_safe_for_llm."""

    def test_safe_text(self) -> None:
        safe, labels = is_safe_for_llm("clean text here")
        assert safe is True
        assert labels == []

    def test_unsafe_text(self) -> None:
        pat = "ghp_" + "a" * 36
        safe, labels = is_safe_for_llm(f"token {pat}")
        assert safe is False
        assert "GITHUB_PAT" in labels
