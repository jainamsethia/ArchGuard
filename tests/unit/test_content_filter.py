def test_secret_redaction_covers_api_keys():
    from archguard.utils.content_filter import redact_secrets

    anthropic = "Here is my key: sk-ant-api03-abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-abcdefghijklmnopqrstuvwxyzABCDE"
    assert "[REDACTED:ANTHROPIC_KEY]" in redact_secrets(anthropic).text

    github = "My github token is ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    assert "[REDACTED:GITHUB_PAT]" in redact_secrets(github).text

    aws = "aws_secret_access_key='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN'"
    assert "[REDACTED:AWS_SECRET_ACCESS_KEY]" in redact_secrets(aws).text

    db = "DATABASE_URL=postgres://user:supersecretpass@localhost:5432/mydb"
    assert "[REDACTED:DATABASE_URL]" in redact_secrets(db).text


def test_a_github_app_installation_token_is_redacted() -> None:
    """``ghs_`` is what a GitHub App installation token looks like.

    The list already covered classic (``ghp_``) and fine-grained
    (``github_pat_``) personal tokens but not this one, which is the credential
    ArchGuard itself mints to clone a private repository. It can reach an LLM
    prompt the same way any other secret can: committed to the analysed
    repository by mistake, or present in a traceback that becomes context.
    """
    from archguard.utils.content_filter import redact_secrets

    text = "remote failed with ghs_16C7e42F292c6912E7710c838347Ae178B4a"
    result = redact_secrets(text)

    assert "ghs_16C7e42F292c6912E7710c838347Ae178B4a" not in result.text
    assert "GITHUB_APP_TOKEN" in result.redactions
