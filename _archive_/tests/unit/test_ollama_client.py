"""Unit tests for tools.ollama_client.

No live Ollama server required — all tests stub ollama.Client via unittest.mock.

The stub (_FakeClient) is deliberately NOT a context manager, matching the real
ollama>=0.4 Client (which has no __enter__/__exit__). This guards against
regressing to the old `with ollama.Client(...)` pattern, which raised TypeError
at runtime against the pinned client and silently no-op'd generate() (issue: the
16GB-machine cloud-generation hotfix).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class _FakeClient:
    """Stand-in for ollama.Client — a plain object, NOT a context manager.

    Using this in a `with` block raises TypeError (no __enter__), so any
    reintroduction of the old context-manager pattern fails the suite. Exposes
    `_client` (the httpx pool) so tests can assert _close_client() closed it.
    """

    def __init__(self, *args, **kwargs):
        self.init_args = args
        self.init_kwargs = kwargs
        self.generate = MagicMock()
        self.chat = MagicMock()
        self._client = MagicMock()  # httpx pool; _close_client() calls .close()


class TestResolveConfig:
    def test_resolve_config_returns_tuple(self):
        """resolve_config() returns a (str, str, float) triple without raising."""
        from tools.ollama_client import resolve_config

        base_url, model, timeout_s = resolve_config()
        assert isinstance(base_url, str)
        assert isinstance(model, str)
        assert isinstance(timeout_s, float)

    def test_resolve_config_defaults(self):
        """With settings unreachable, falls back to ModelSettings Pydantic defaults."""
        from tools.ollama_client import resolve_config

        # The defaults are defined in config/settings.py — just check they're non-empty.
        base_url, model, timeout_s = resolve_config()
        assert base_url.startswith("http")
        assert model  # non-empty
        assert timeout_s > 0


class TestGenerate:
    def _make_response(self, text: str):
        """Build a minimal stub that looks like an ollama generate response."""
        resp = MagicMock()
        resp.response = text
        return resp

    def test_generate_extracts_string(self):
        """Strips whitespace from the response text and returns it."""
        from tools.ollama_client import generate

        fake = _FakeClient()
        fake.generate.return_value = self._make_response("  hello  ")

        with patch("ollama.Client", return_value=fake):
            result = generate("my prompt", model="m", timeout_s=5.0, base_url="http://localhost")

        assert result == "hello"

    def test_generate_returns_none_on_empty_response(self):
        """Returns None when the Ollama response text is empty/whitespace."""
        from tools.ollama_client import generate

        fake = _FakeClient()
        fake.generate.return_value = self._make_response("")

        with patch("ollama.Client", return_value=fake):
            result = generate("prompt", model="m", timeout_s=5.0, base_url="http://localhost")

        assert result is None

    def test_generate_returns_none_on_exception(self):
        """Returns None when the client raises (fail-silent contract)."""
        from tools.ollama_client import generate

        fake = _FakeClient()
        fake.generate.side_effect = ConnectionRefusedError("ollama down")

        with patch("ollama.Client", return_value=fake):
            result = generate("prompt", model="m", timeout_s=5.0, base_url="http://localhost")

        assert result is None

    def test_generate_closes_httpx_pool(self):
        """The httpx socket pool is closed after generate() (even on the happy path)."""
        from tools.ollama_client import generate

        fake = _FakeClient()
        fake.generate.return_value = self._make_response("ok")

        with patch("ollama.Client", return_value=fake):
            generate("prompt", model="m", timeout_s=5.0, base_url="http://localhost")

        fake._client.close.assert_called_once()

    def test_generate_closes_httpx_pool_on_exception(self):
        """The httpx pool is closed even when the request raises (finally block)."""
        from tools.ollama_client import generate

        fake = _FakeClient()
        fake.generate.side_effect = ConnectionRefusedError("ollama down")

        with patch("ollama.Client", return_value=fake):
            generate("prompt", model="m", timeout_s=5.0, base_url="http://localhost")

        fake._client.close.assert_called_once()

    def test_generate_uses_provided_base_url(self):
        """The base_url argument is forwarded to ollama.Client(host=...)."""
        from tools.ollama_client import generate

        fake = _FakeClient()
        fake.generate.return_value = self._make_response("text")

        with patch("ollama.Client", return_value=fake) as mock_cls:
            generate("prompt", model="my-model", timeout_s=3.0, base_url="http://custom:9999")

        mock_cls.assert_called_once_with(host="http://custom:9999", timeout=3.0)


class TestChat:
    def _make_chat_response(self, content: str):
        """Build a minimal stub that looks like an ollama chat response."""
        msg = MagicMock()
        msg.content = content
        resp = MagicMock()
        resp.message = msg
        return resp

    def test_chat_extracts_content(self):
        """Returns the assistant message content string."""
        from tools.ollama_client import chat

        fake = _FakeClient()
        fake.chat.return_value = self._make_chat_response("spam")

        with patch("ollama.Client", return_value=fake):
            result = chat(
                [{"role": "user", "content": "hello"}],
                model="m",
                base_url="http://localhost",
            )

        assert result == "spam"

    def test_chat_raises_on_failure(self):
        """chat() propagates exceptions — callers rely on raise-to-escalate."""
        from tools.ollama_client import chat

        fake = _FakeClient()
        fake.chat.side_effect = RuntimeError("ollama down")

        with patch("ollama.Client", return_value=fake):
            with pytest.raises(RuntimeError, match="ollama down"):
                chat(
                    [{"role": "user", "content": "hi"}],
                    model="m",
                    base_url="http://localhost",
                )

    def test_chat_closes_httpx_pool(self):
        """The httpx socket pool is closed after chat()."""
        from tools.ollama_client import chat

        fake = _FakeClient()
        fake.chat.return_value = self._make_chat_response("ok")

        with patch("ollama.Client", return_value=fake):
            chat([{"role": "user", "content": "hi"}], model="m", base_url="http://localhost")

        fake._client.close.assert_called_once()

    def test_chat_closes_httpx_pool_on_exception(self):
        """The httpx pool is closed even when chat() raises (finally block)."""
        from tools.ollama_client import chat

        fake = _FakeClient()
        fake.chat.side_effect = RuntimeError("ollama down")

        with patch("ollama.Client", return_value=fake):
            with pytest.raises(RuntimeError):
                chat([{"role": "user", "content": "hi"}], model="m", base_url="http://localhost")

        fake._client.close.assert_called_once()

    def test_chat_passes_options(self):
        """options dict is forwarded to client.chat() when provided."""
        from tools.ollama_client import chat

        fake = _FakeClient()
        fake.chat.return_value = self._make_chat_response("reply")

        with patch("ollama.Client", return_value=fake):
            chat(
                [{"role": "user", "content": "hi"}],
                model="m",
                base_url="http://localhost",
                options={"temperature": 0},
            )

        call_kwargs = fake.chat.call_args
        assert call_kwargs.kwargs.get("options") == {"temperature": 0}

    def test_chat_no_timeout_by_default(self):
        """Without timeout_s, Client is not passed a timeout (preserves prior behavior)."""
        from tools.ollama_client import chat

        fake = _FakeClient()
        fake.chat.return_value = self._make_chat_response("ok")

        with patch("ollama.Client", return_value=fake) as mock_cls:
            chat([{"role": "user", "content": "hi"}], model="m", base_url="http://localhost")

        # timeout should NOT be in the Client constructor kwargs
        call_kwargs = mock_cls.call_args
        assert "timeout" not in call_kwargs.kwargs
