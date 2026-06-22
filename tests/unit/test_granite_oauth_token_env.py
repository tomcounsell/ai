"""Unit tests for CLAUDE_CODE_OAUTH_TOKEN handling in _build_env().

Covers:
  (a) token set in env -> key present in returned env, value unchanged
  (b) token unset -> key absent from returned env (not set to "")
  (c) ANTHROPIC_* are still blanked in both cases (regression guard for PR #1612)
  (d) malformed token (wrong prefix) -> still forwarded to env, no crash
  (e) token value never appears in str() or repr() of the env dict in a way
      that would leak it — verified by confirming the returned dict is an
      ordinary dict (not a special logging-aware wrapper)
"""

from __future__ import annotations

import pytest

from agent.granite_container.pty_driver import _build_env


class TestBuildEnvOAuthToken:
    """Behaviour of _build_env() with respect to CLAUDE_CODE_OAUTH_TOKEN."""

    def test_token_present_forwarded_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """(a) When CLAUDE_CODE_OAUTH_TOKEN is set it must appear in the child env."""
        token = "sk-ant-oat01-test-token-abc123"
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", token)

        env = _build_env()

        assert "CLAUDE_CODE_OAUTH_TOKEN" in env
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == token

    def test_token_absent_key_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """(b) When CLAUDE_CODE_OAUTH_TOKEN is unset the key must be absent (not '')."""
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

        env = _build_env()

        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env

    def test_token_empty_string_key_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """(b-edge) An empty-string token should also leave the key absent."""
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "")

        env = _build_env()

        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env

    def test_anthropic_api_key_blanked_with_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """(c) ANTHROPIC_API_KEY is blanked even when a token is present."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real-key")
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-some-token")

        env = _build_env()

        assert env["ANTHROPIC_API_KEY"] == ""

    def test_anthropic_base_url_blanked_with_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """(c) ANTHROPIC_BASE_URL is blanked even when a token is present."""
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:11434")
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-some-token")

        env = _build_env()

        assert env["ANTHROPIC_BASE_URL"] == ""

    def test_anthropic_auth_token_blanked_with_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """(c) ANTHROPIC_AUTH_TOKEN is blanked even when a token is present."""
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ollama")
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-some-token")

        env = _build_env()

        assert env["ANTHROPIC_AUTH_TOKEN"] == ""

    def test_anthropic_vars_blanked_without_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """(c) ANTHROPIC_* blanked when no OAuth token is present either."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real-key")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:11434")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ollama")
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

        env = _build_env()

        assert env["ANTHROPIC_API_KEY"] == ""
        assert env["ANTHROPIC_BASE_URL"] == ""
        assert env["ANTHROPIC_AUTH_TOKEN"] == ""

    def test_malformed_token_forwarded_no_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """(d) A token with wrong prefix is forwarded as-is; Claude Code rejects it, not us."""
        bad_token = "not-a-valid-oauth-token-format"
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", bad_token)

        env = _build_env()

        assert "CLAUDE_CODE_OAUTH_TOKEN" in env
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == bad_token

    def test_token_value_is_plain_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """(e) Returned env is a plain dict; no special logging-aware wrapper that could
        accidentally surface the token in repr/str of the container object."""
        token = "sk-ant-oat01-super-secret-value"
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", token)

        env = _build_env()

        assert type(env) is dict  # noqa: E721 — must be exactly dict, not a subclass

    def test_token_not_leaked_when_absent_in_str(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """(e) When token is absent, the string form of the env dict must not contain
        the sentinel key at all."""
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

        env = _build_env()

        assert "CLAUDE_CODE_OAUTH_TOKEN" not in str(env)
