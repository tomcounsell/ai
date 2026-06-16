"""Unit tests for tools.ollama_client.

No live Ollama server required — all tests stub ollama.Client via unittest.mock.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


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

        stub_response = self._make_response("  hello  ")

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.generate.return_value = stub_response

        with patch("ollama.Client", return_value=mock_client):
            result = generate("my prompt", model="m", timeout_s=5.0, base_url="http://localhost")

        assert result == "hello"

    def test_generate_returns_none_on_empty_response(self):
        """Returns None when the Ollama response text is empty/whitespace."""
        from tools.ollama_client import generate

        stub_response = self._make_response("")

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.generate.return_value = stub_response

        with patch("ollama.Client", return_value=mock_client):
            result = generate("prompt", model="m", timeout_s=5.0, base_url="http://localhost")

        assert result is None

    def test_generate_returns_none_on_exception(self):
        """Returns None when the client raises (fail-silent contract)."""
        from tools.ollama_client import generate

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.generate.side_effect = ConnectionRefusedError("ollama down")

        with patch("ollama.Client", return_value=mock_client):
            result = generate("prompt", model="m", timeout_s=5.0, base_url="http://localhost")

        assert result is None

    def test_client_context_managed_on_generate(self):
        """Client is used as a context manager (__exit__ is called)."""
        from tools.ollama_client import generate

        stub_response = self._make_response("ok")

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.generate.return_value = stub_response

        with patch("ollama.Client", return_value=mock_client):
            generate("prompt", model="m", timeout_s=5.0, base_url="http://localhost")

        mock_client.__exit__.assert_called_once()

    def test_generate_uses_provided_base_url(self):
        """The base_url argument is forwarded to ollama.Client(host=...)."""
        from tools.ollama_client import generate

        stub_response = self._make_response("text")
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.generate.return_value = stub_response

        with patch("ollama.Client", return_value=mock_client) as mock_cls:
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

        stub_response = self._make_chat_response("spam")

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.chat.return_value = stub_response

        with patch("ollama.Client", return_value=mock_client):
            result = chat(
                [{"role": "user", "content": "hello"}],
                model="m",
                base_url="http://localhost",
            )

        assert result == "spam"

    def test_chat_raises_on_failure(self):
        """chat() propagates exceptions — callers rely on raise-to-escalate."""
        from tools.ollama_client import chat

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.chat.side_effect = RuntimeError("ollama down")

        with patch("ollama.Client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="ollama down"):
                chat(
                    [{"role": "user", "content": "hi"}],
                    model="m",
                    base_url="http://localhost",
                )

    def test_client_context_managed_on_chat(self):
        """Client is used as a context manager (__exit__ is called)."""
        from tools.ollama_client import chat

        stub_response = self._make_chat_response("ok")
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.chat.return_value = stub_response

        with patch("ollama.Client", return_value=mock_client):
            chat([{"role": "user", "content": "hi"}], model="m", base_url="http://localhost")

        mock_client.__exit__.assert_called_once()

    def test_chat_passes_options(self):
        """options dict is forwarded to client.chat() when provided."""
        from tools.ollama_client import chat

        stub_response = self._make_chat_response("reply")
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.chat.return_value = stub_response

        with patch("ollama.Client", return_value=mock_client):
            chat(
                [{"role": "user", "content": "hi"}],
                model="m",
                base_url="http://localhost",
                options={"temperature": 0},
            )

        call_kwargs = mock_client.chat.call_args
        assert call_kwargs.kwargs.get("options") == {"temperature": 0}

    def test_chat_no_timeout_by_default(self):
        """Without timeout_s, Client is not passed a timeout (preserves prior behavior)."""
        from tools.ollama_client import chat

        stub_response = self._make_chat_response("ok")
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.chat.return_value = stub_response

        with patch("ollama.Client", return_value=mock_client) as mock_cls:
            chat([{"role": "user", "content": "hi"}], model="m", base_url="http://localhost")

        # timeout should NOT be in the Client constructor kwargs
        call_kwargs = mock_cls.call_args
        assert "timeout" not in call_kwargs.kwargs
