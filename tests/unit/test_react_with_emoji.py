"""Unit tests for ``tools.react_with_emoji``.

Covers the two CLI surfaces the tool exposes after the ``send_telegram.py``
retirement (``docs/plans/consolidate_delivery_paths.md`` Decision C):

* ``react(feeling)`` — a reaction on an existing message (``type: reaction``).
  These are the ported ``TestSendTelegramReaction`` cases from the deleted
  ``tests/unit/test_send_telegram.py``.
* ``standalone(feeling)`` — the migrated home of the retired ``send_emoji``
  ``--emoji`` capability: a custom-emoji *message* in its own bubble
  (``type: custom_emoji_message``). The plan requires the outbox payload
  shape to be identical to the old ``send_emoji`` so the relay needs no
  change — the contract tests below pin that shape byte-for-byte against the
  historical schema.

The real tool functions run; only the Redis client (``_get_redis``) is
replaced with a MagicMock and only ``find_best_emoji`` (the embedding lookup)
is stubbed so the resolved emoji is deterministic without loading the
sticker-set embeddings. Nothing about the tool's own payload construction is
mocked.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# The canonical key set the OLD send_emoji produced for a custom-emoji message.
# The migrated --standalone payload MUST match this shape exactly (plus the
# optional custom_emoji_document_id when the resolved emoji is custom).
_OLD_SEND_EMOJI_KEYS = {
    "type",
    "chat_id",
    "reply_to",
    "emoji",
    "session_id",
    "timestamp",
}


@pytest.fixture
def _telegram_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("TELEGRAM_REPLY_TO", "67890")
    monkeypatch.setenv("VALOR_SESSION_ID", "test-session")
    monkeypatch.delenv("EMAIL_REPLY_TO", raising=False)
    monkeypatch.delenv("VALOR_TRANSPORT", raising=False)


def _queued_payload(mock_redis: MagicMock) -> dict:
    """Return the JSON payload passed to the single rpush call."""
    mock_redis.rpush.assert_called_once()
    return json.loads(mock_redis.rpush.call_args[0][1])


class TestReact:
    """Ported ``TestSendTelegramReaction`` — reaction on an existing message."""

    def test_react_queues_reaction_payload(self, _telegram_env):
        """A standard-emoji reaction has type='reaction' and no custom doc id."""
        from tools.emoji_embedding import EmojiResult
        from tools.react_with_emoji import react

        mock_redis = MagicMock()
        with (
            patch("tools.react_with_emoji._get_redis", return_value=mock_redis),
            patch(
                "tools.emoji_embedding.find_best_emoji",
                return_value=EmojiResult(emoji="\U0001f525"),
            ),
        ):
            react("excited")

        payload = _queued_payload(mock_redis)
        assert payload["type"] == "reaction"
        assert payload["emoji"] == "\U0001f525"
        assert payload["chat_id"] == "12345"
        assert payload["reply_to"] == 67890
        assert payload["session_id"] == "test-session"
        assert "custom_emoji_document_id" not in payload
        # Reactions land on the session-scoped outbox queue.
        assert mock_redis.rpush.call_args[0][0] == "telegram:outbox:test-session"

    def test_react_queues_custom_emoji_reaction(self, _telegram_env):
        """A custom emoji reaction carries custom_emoji_document_id."""
        from tools.emoji_embedding import EmojiResult
        from tools.react_with_emoji import react

        mock_redis = MagicMock()
        with (
            patch("tools.react_with_emoji._get_redis", return_value=mock_redis),
            patch(
                "tools.emoji_embedding.find_best_emoji",
                return_value=EmojiResult(emoji="\U0001f525", document_id=99999, is_custom=True),
            ),
        ):
            react("excited")

        payload = _queued_payload(mock_redis)
        assert payload["type"] == "reaction"
        assert payload["custom_emoji_document_id"] == 99999

    def test_react_requires_reply_to(self, monkeypatch):
        """Missing TELEGRAM_REPLY_TO → exit 1 (a reaction needs an anchor)."""
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setenv("VALOR_SESSION_ID", "test-session")
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)
        monkeypatch.delenv("EMAIL_REPLY_TO", raising=False)
        monkeypatch.delenv("VALOR_TRANSPORT", raising=False)

        from tools.react_with_emoji import react

        with pytest.raises(SystemExit) as exc_info:
            react("happy")
        assert exc_info.value.code == 1

    def test_react_empty_feeling_exits(self, _telegram_env):
        """Empty feeling → exit 1."""
        from tools.react_with_emoji import react

        with pytest.raises(SystemExit) as exc_info:
            react("")
        assert exc_info.value.code == 1

    def test_react_cli_flag(self, _telegram_env):
        """``main()`` with no --standalone flag dispatches to react()."""
        from tools.emoji_embedding import EmojiResult
        from tools.react_with_emoji import main

        mock_redis = MagicMock()
        with (
            patch("tools.react_with_emoji._get_redis", return_value=mock_redis),
            patch(
                "tools.emoji_embedding.find_best_emoji",
                return_value=EmojiResult(emoji="\U0001f44d"),
            ),
            patch("sys.argv", ["react_with_emoji.py", "happy"]),
        ):
            main()

        payload = _queued_payload(mock_redis)
        assert payload["type"] == "reaction"
        assert payload["emoji"] == "\U0001f44d"


class TestStandalone:
    """Migrated ``send_emoji`` (--standalone) — custom-emoji standalone message.

    The payload shape MUST equal the retired send_emoji shape so the relay
    needs no change (plan Success Criterion: "``react_with_emoji.py
    --standalone`` sends a ``custom_emoji_message`` payload identical in shape
    to the old ``send_emoji``").
    """

    def test_standalone_queues_custom_emoji_message(self, _telegram_env):
        """--standalone with a custom emoji → custom_emoji_message payload."""
        from tools.emoji_embedding import EmojiResult
        from tools.react_with_emoji import standalone

        mock_redis = MagicMock()
        with (
            patch("tools.react_with_emoji._get_redis", return_value=mock_redis),
            patch(
                "tools.emoji_embedding.find_best_emoji",
                return_value=EmojiResult(emoji="\U0001f389", document_id=42, is_custom=True),
            ),
        ):
            standalone("celebration")

        payload = _queued_payload(mock_redis)
        assert payload["type"] == "custom_emoji_message"
        assert payload["emoji"] == "\U0001f389"
        assert payload["custom_emoji_document_id"] == 42
        assert payload["chat_id"] == "12345"
        assert payload["session_id"] == "test-session"
        assert mock_redis.rpush.call_args[0][0] == "telegram:outbox:test-session"

    def test_standalone_payload_shape_identical_to_old_send_emoji(self, _telegram_env):
        """The standalone payload key set must equal the historical send_emoji
        schema exactly (contract: relay needs no change)."""
        from tools.emoji_embedding import EmojiResult
        from tools.react_with_emoji import standalone

        # Custom emoji → base keys PLUS custom_emoji_document_id.
        mock_custom = MagicMock()
        with (
            patch("tools.react_with_emoji._get_redis", return_value=mock_custom),
            patch(
                "tools.emoji_embedding.find_best_emoji",
                return_value=EmojiResult(emoji="\U0001f389", document_id=42, is_custom=True),
            ),
        ):
            standalone("celebration")
        custom_payload = _queued_payload(mock_custom)
        assert set(custom_payload.keys()) == _OLD_SEND_EMOJI_KEYS | {"custom_emoji_document_id"}
        assert custom_payload["type"] == "custom_emoji_message"

        # Standard-emoji fallback → exactly the base keys (no custom doc id).
        mock_std = MagicMock()
        with (
            patch("tools.react_with_emoji._get_redis", return_value=mock_std),
            patch(
                "tools.emoji_embedding.find_best_emoji",
                return_value=EmojiResult(emoji="\U0001f525"),
            ),
        ):
            standalone("fire")
        std_payload = _queued_payload(mock_std)
        assert set(std_payload.keys()) == _OLD_SEND_EMOJI_KEYS
        assert "custom_emoji_document_id" not in std_payload
        assert std_payload["type"] == "custom_emoji_message"

    def test_standalone_standard_fallback_without_reply_to(self, monkeypatch):
        """--standalone works without TELEGRAM_REPLY_TO (optional for messages)."""
        from tools.emoji_embedding import EmojiResult
        from tools.react_with_emoji import standalone

        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
        monkeypatch.setenv("VALOR_SESSION_ID", "test-session")
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)
        monkeypatch.delenv("EMAIL_REPLY_TO", raising=False)
        monkeypatch.delenv("VALOR_TRANSPORT", raising=False)

        mock_redis = MagicMock()
        with (
            patch("tools.react_with_emoji._get_redis", return_value=mock_redis),
            patch(
                "tools.emoji_embedding.find_best_emoji",
                return_value=EmojiResult(emoji="\U0001f525"),
            ),
        ):
            standalone("fire")

        payload = _queued_payload(mock_redis)
        assert payload["type"] == "custom_emoji_message"
        assert payload["emoji"] == "\U0001f525"
        assert payload["reply_to"] is None
        assert "custom_emoji_document_id" not in payload

    def test_standalone_empty_feeling_exits(self, _telegram_env):
        """--standalone with an empty feeling → exit 1."""
        from tools.react_with_emoji import standalone

        with pytest.raises(SystemExit) as exc_info:
            standalone("")
        assert exc_info.value.code == 1

    def test_standalone_whitespace_feeling_exits(self, _telegram_env):
        """--standalone with a whitespace-only feeling → exit 1."""
        from tools.react_with_emoji import standalone

        with pytest.raises(SystemExit) as exc_info:
            standalone("   ")
        assert exc_info.value.code == 1

    def test_standalone_missing_chat_id_exits(self, monkeypatch):
        """--standalone with no TELEGRAM_CHAT_ID → exit 1."""
        monkeypatch.setenv("VALOR_SESSION_ID", "test-session")
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.delenv("EMAIL_REPLY_TO", raising=False)
        monkeypatch.delenv("VALOR_TRANSPORT", raising=False)

        from tools.react_with_emoji import standalone

        with pytest.raises(SystemExit) as exc_info:
            standalone("happy")
        assert exc_info.value.code == 1

    def test_standalone_cli_flag(self, _telegram_env):
        """``main()`` with --standalone dispatches to standalone()."""
        from tools.emoji_embedding import EmojiResult
        from tools.react_with_emoji import main

        mock_redis = MagicMock()
        with (
            patch("tools.react_with_emoji._get_redis", return_value=mock_redis),
            patch(
                "tools.emoji_embedding.find_best_emoji",
                return_value=EmojiResult(emoji="\U0001f525"),
            ),
            patch("sys.argv", ["react_with_emoji.py", "--standalone", "excited"]),
        ):
            main()

        payload = _queued_payload(mock_redis)
        assert payload["type"] == "custom_emoji_message"
        assert payload["emoji"] == "\U0001f525"

    def test_standalone_email_transport_is_noop(self, monkeypatch):
        """On an email session, --standalone is a no-op (no outbox write)."""
        from tools.react_with_emoji import standalone

        monkeypatch.setenv("VALOR_SESSION_ID", "test-session")
        monkeypatch.setenv("EMAIL_REPLY_TO", "alice@example.com")
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.setenv("VALOR_TRANSPORT", "email")

        mock_redis = MagicMock()
        with patch("tools.react_with_emoji._get_redis", return_value=mock_redis):
            # Must not raise and must not queue anything.
            standalone("celebration")
        mock_redis.rpush.assert_not_called()
