"""Tests for duh.kernel.redact — secrets redaction."""

from __future__ import annotations

import pytest

from duh.kernel.redact import redact_secrets, REDACTED


class TestRedactSecrets:
    def test_no_secrets(self):
        text = "Hello, world! Just a normal message."
        assert redact_secrets(text) == text

    def test_anthropic_api_key(self):
        text = "Key is sk-ant-api03-abc123xyz"
        result = redact_secrets(text)
        assert "sk-ant" not in result
        assert REDACTED in result

    def test_openai_api_key(self):
        text = "export OPENAI_API_KEY=sk-proj-abc123def456"
        result = redact_secrets(text)
        assert "sk-proj" not in result
        assert REDACTED in result

    def test_aws_access_key(self):
        text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        result = redact_secrets(text)
        assert "AKIA" not in result
        assert REDACTED in result

    def test_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.test.sig"
        result = redact_secrets(text)
        assert "eyJhbGci" not in result
        assert REDACTED in result

    def test_github_token(self):
        text = "GITHUB_TOKEN=ghp_abc123def456ghi789jkl012"
        result = redact_secrets(text)
        assert "ghp_" not in result
        assert REDACTED in result

    def test_generic_private_key(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
        result = redact_secrets(text)
        assert "PRIVATE KEY" not in result
        assert REDACTED in result

    def test_multiple_secrets_in_one_string(self):
        text = "Key1=sk-ant-api03-abc123 and Key2=AKIAIOSFODNN7EXAMPLE"
        result = redact_secrets(text)
        assert "sk-ant" not in result
        assert "AKIA" not in result
        assert result.count(REDACTED) == 2

    def test_empty_string(self):
        assert redact_secrets("") == ""

    def test_password_in_url(self):
        text = "postgres://user:s3cretP@ss@localhost:5432/db"
        result = redact_secrets(text)
        assert "s3cretP@ss" not in result

    def test_generic_secret_assignment(self):
        text = 'SECRET_KEY="my-super-secret-value-12345"'
        result = redact_secrets(text)
        assert "my-super-secret" not in result

    def test_short_values_not_redacted(self):
        """Short values after SECRET_KEY= should still be redacted."""
        text = 'API_KEY="abc"'
        result = redact_secrets(text)
        # Even short values after a key-like name are redacted
        assert REDACTED in result

    def test_preserves_surrounding_text(self):
        text = "Config loaded. API key: sk-ant-api03-xyz. Continuing."
        result = redact_secrets(text)
        assert result.startswith("Config loaded.")
        assert result.endswith("Continuing.")


class TestRedactSecretsEdgeCases:
    """Additional edge cases for robustness."""

    def test_ec_private_key(self):
        text = "-----BEGIN EC PRIVATE KEY-----\ndata\n-----END EC PRIVATE KEY-----"
        result = redact_secrets(text)
        assert "PRIVATE KEY" not in result

    def test_openssh_private_key(self):
        text = "-----BEGIN OPENSSH PRIVATE KEY-----\ndata\n-----END OPENSSH PRIVATE KEY-----"
        result = redact_secrets(text)
        assert "PRIVATE KEY" not in result

    def test_gho_token(self):
        text = "token=gho_abc123def456ghi789jkl012"
        result = redact_secrets(text)
        assert "gho_" not in result

    def test_ghs_token(self):
        text = "token=ghs_abc123def456ghi789jkl012"
        result = redact_secrets(text)
        assert "ghs_" not in result

    def test_bearer_case_insensitive(self):
        text = "bearer abc123def456ghi789"
        result = redact_secrets(text)
        assert "abc123def456" not in result

    def test_sk_long_key(self):
        """Generic sk- key long enough to match."""
        text = "key=sk-abcdefghij1234567890ab"
        result = redact_secrets(text)
        assert "sk-abcdefghij" not in result

    def test_multiple_urls_with_passwords(self):
        text = "db1=mysql://root:pass1@host1 db2=postgres://admin:pass2@host2"
        result = redact_secrets(text)
        assert "pass1" not in result
        assert "pass2" not in result


class TestRedactInNativeExecutor:
    """Verify redaction is wired into tool output."""

    async def test_native_executor_redacts_output(self):
        """NativeExecutor.run() should redact secrets from tool output."""
        from duh.kernel.tool import Tool, ToolContext, ToolResult
        from duh.adapters.native_executor import NativeExecutor

        class LeakyTool(Tool):
            name = "Leaky"
            description = "Returns a secret"
            input_schema = {"type": "object", "properties": {}}

            async def call(self, input: dict, ctx: ToolContext) -> ToolResult:
                return ToolResult(output="Secret: sk-ant-api03-abcdef1234567890abcdef")

        executor = NativeExecutor(tools=[LeakyTool()], redact=True)
        result = await executor.run("Leaky", {})
        assert "sk-ant" not in result
        assert REDACTED in result
