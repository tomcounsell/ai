"""Unit tests for bridge.telegram_bridge.should_store_inbound (issue #2020).

should_store_inbound gates Redis persistence of inbound Telegram messages to
machine-owned chats, plus a carve-out for registered bots (needed for the
valor-telegram --await-reply E2E flow, issue #1574).
"""

from bridge.telegram_bridge import should_store_inbound


def test_owned_project_key_stores_regardless_of_sender(monkeypatch):
    """A resolved (non-None) early_project_key always means store."""
    monkeypatch.setattr("bridge.telegram_bridge.find_project_for_bot", lambda sender_id: None)
    assert should_store_inbound("psyoptimal", None) is True
    assert should_store_inbound("psyoptimal", 12345) is True


def test_unowned_chat_with_no_sender_does_not_store(monkeypatch):
    monkeypatch.setattr("bridge.telegram_bridge.find_project_for_bot", lambda sender_id: None)
    assert should_store_inbound(None, None) is False


def test_unowned_chat_with_unregistered_sender_does_not_store(monkeypatch):
    monkeypatch.setattr("bridge.telegram_bridge.find_project_for_bot", lambda sender_id: None)
    assert should_store_inbound(None, 99999) is False


def test_unowned_chat_with_registered_bot_sender_stores(monkeypatch):
    monkeypatch.setattr(
        "bridge.telegram_bridge.find_project_for_bot",
        lambda sender_id: {"_key": "some-project"} if sender_id == 8837490628 else None,
    )
    assert should_store_inbound(None, 8837490628) is True


def test_never_raises_on_none_none(monkeypatch):
    monkeypatch.setattr("bridge.telegram_bridge.find_project_for_bot", lambda sender_id: None)
    assert should_store_inbound(None, None) is False
