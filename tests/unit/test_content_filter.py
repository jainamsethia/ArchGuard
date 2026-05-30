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
