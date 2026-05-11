"""
Tests for `tools.install.prompt` — the harness-agnostic prompt shim used by
the /setup skill to gather user input for the vault picker.

Adapter selection cascade:
  1. ``VALOR_HARNESS=claude-code`` → emit JSON on stdout, raise InstallPromptDeferred
  2. ``sys.stdin.isatty()`` → readline-based prompt
  3. otherwise → raise InstallPromptUnavailable
"""

import io
import json
import sys
from contextlib import redirect_stdout

import pytest

from tools.install.prompt import (
    InstallPromptDeferred,
    InstallPromptUnavailable,
    ask_choice,
    ask_input,
)

# ---------------------------------------------------------------------------
# TTY adapter
# ---------------------------------------------------------------------------


class TestTTYAdapter:
    def test_ask_choice_via_tty_returns_selected_option(self, monkeypatch):
        """User types '2', helper returns the second option's value."""
        monkeypatch.delenv("VALOR_HARNESS", raising=False)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "2")

        result = ask_choice(
            "Pick a vault location",
            options=[
                {"label": "~/.valor", "value": "~/.valor"},
                {"label": "~/Documents/Valor", "value": "~/Documents/Valor"},
                {"label": "~/Desktop/Valor", "value": "~/Desktop/Valor"},
            ],
            header="Vault path",
        )

        assert result == "~/Documents/Valor"

    def test_ask_choice_rejects_invalid_index_then_accepts(self, monkeypatch):
        """User types '99' (invalid), then '1' (valid). Returns first option."""
        monkeypatch.delenv("VALOR_HARNESS", raising=False)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        responses = iter(["99", "abc", "1"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(responses))

        result = ask_choice(
            "Pick",
            options=[{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
            header="X",
        )
        assert result == "a"

    def test_ask_input_via_tty_returns_user_text(self, monkeypatch):
        monkeypatch.delenv("VALOR_HARNESS", raising=False)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "/Users/me/somewhere")

        result = ask_input("Enter vault path", header="Vault path")
        assert result == "/Users/me/somewhere"

    def test_ask_input_via_tty_returns_default_on_empty_input(self, monkeypatch):
        monkeypatch.delenv("VALOR_HARNESS", raising=False)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")

        result = ask_input("X", header="X", default="/fallback")
        assert result == "/fallback"

    def test_ask_input_validator_rejects_then_accepts(self, monkeypatch):
        monkeypatch.delenv("VALOR_HARNESS", raising=False)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        responses = iter(["bad", "good"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(responses))

        def validator(s: str) -> str | None:
            return None if s == "good" else "must be 'good'"

        result = ask_input("X", header="X", validator=validator)
        assert result == "good"


# ---------------------------------------------------------------------------
# Claude Code adapter (deferred / JSON instruction)
# ---------------------------------------------------------------------------


class TestClaudeCodeAdapter:
    def test_ask_choice_emits_json_and_raises_deferred(self, monkeypatch):
        monkeypatch.setenv("VALOR_HARNESS", "claude-code")

        buf = io.StringIO()
        with redirect_stdout(buf):
            with pytest.raises(InstallPromptDeferred):
                ask_choice(
                    "Pick a vault location",
                    options=[
                        {"label": "~/.valor", "value": "~/.valor", "description": "Hidden home"},
                        {"label": "Custom", "value": "__custom__"},
                    ],
                    header="Vault path",
                )

        out = json.loads(buf.getvalue())
        assert out["kind"] == "ask_choice"
        assert out["question"] == "Pick a vault location"
        assert out["header"] == "Vault path"
        assert len(out["options"]) == 2
        assert out["options"][0]["label"] == "~/.valor"
        assert out["options"][0]["description"] == "Hidden home"

    def test_ask_input_emits_json_and_raises_deferred(self, monkeypatch):
        monkeypatch.setenv("VALOR_HARNESS", "claude-code")

        buf = io.StringIO()
        with redirect_stdout(buf):
            with pytest.raises(InstallPromptDeferred):
                ask_input("Enter custom vault path", header="Vault path", default="~/Valor")

        out = json.loads(buf.getvalue())
        assert out["kind"] == "ask_input"
        assert out["question"] == "Enter custom vault path"
        assert out["default"] == "~/Valor"


# ---------------------------------------------------------------------------
# No-harness fallback
# ---------------------------------------------------------------------------


class TestNoHarnessFallback:
    def test_ask_choice_raises_unavailable_when_no_tty_and_no_harness(self, monkeypatch):
        monkeypatch.delenv("VALOR_HARNESS", raising=False)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

        with pytest.raises(InstallPromptUnavailable):
            ask_choice("Pick", options=[{"label": "A", "value": "a"}], header="X")

    def test_ask_input_raises_unavailable_when_no_tty_and_no_harness(self, monkeypatch):
        monkeypatch.delenv("VALOR_HARNESS", raising=False)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

        with pytest.raises(InstallPromptUnavailable):
            ask_input("Enter", header="X")
