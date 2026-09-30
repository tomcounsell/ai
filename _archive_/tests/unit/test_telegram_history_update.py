"""Tests for tools.telegram_history.update_message_text (issue #1574).

Runs against the autouse redis_test_db fixture (isolated db), so reads/writes
never touch production. update_message_text upserts the streamed-edit body onto
the SAME record (no duplicate rows) and is fail-open on a missing record.
"""

from __future__ import annotations

from tools.telegram_history import (
    get_recent_messages,
    store_message,
    update_message_text,
)


def test_update_existing_record_in_place():
    chat_id = "8837490628"
    store_message(chat_id=chat_id, content="partial", sender="bot", message_id=101)

    ok = update_message_text(chat_id, 101, "the full final answer")
    assert ok is True

    recent = get_recent_messages(chat_id, limit=10)
    # message_id round-trips as a string through the store; compare as str.
    bodies = [m["content"] for m in recent["messages"] if str(m["message_id"]) == "101"]
    # Exactly one record, updated in place — no duplicate row.
    assert bodies == ["the full final answer"]


def test_update_missing_record_returns_false_no_phantom_row():
    chat_id = "8837490628"
    # No record with message_id=999 exists.
    ok = update_message_text(chat_id, 999, "edit before insert")
    assert ok is False

    recent = get_recent_messages(chat_id, limit=10)
    assert all(str(m["message_id"]) != "999" for m in recent["messages"])


def test_repeated_edits_keep_single_record():
    chat_id = "8837490628"
    store_message(chat_id=chat_id, content="t", sender="bot", message_id=202)

    for body in ("the", "the answer", "the answer is 42."):
        assert update_message_text(chat_id, 202, body) is True

    recent = get_recent_messages(chat_id, limit=10)
    matches = [m for m in recent["messages"] if str(m["message_id"]) == "202"]
    assert len(matches) == 1
    assert matches[0]["content"] == "the answer is 42."
