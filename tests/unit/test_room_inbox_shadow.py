"""Shadow-mode durable Room-inbox append (Task 11 phase 1, issue #2494).

Spike-4 cutover checklist phase 1: the durable append is written ALONGSIDE
the untouched dispatch flow (not authoritative), so production parity can be
verified before the authoritative cutover ships in a separate release. The
append must never raise into the live intake path.
"""

import uuid
from datetime import UTC, datetime

import pytest

from bridge.room_inbox import shadow_append_inbox
from models.room import Room, telegram_addressee


@pytest.fixture
def scratch_project():
    key = f"test-inbox-{uuid.uuid4().hex[:8]}"
    project = {"_key": key, "working_directory": "/tmp/x"}
    yield project
    for room in Room.query.filter(project_key=key):
        room.clear_inbox()
        room.delete()


class TestShadowAppend:
    def test_appends_message_identity_to_room_inbox(self, scratch_project):
        chat_id = -100777
        ok = shadow_append_inbox(
            scratch_project,
            chat_id=chat_id,
            message_id=42,
            sender_id=7,
            sender_name="Tom",
            text="hello room",
            date=datetime(2026, 8, 7, 12, 0, 0, tzinfo=UTC),
        )
        assert ok is True

        room = Room.resolve(scratch_project["_key"], telegram_addressee(chat_id))
        entries = room.peek_inbox()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["chat_id"] == str(chat_id)
        assert entry["message_id"] == 42
        assert entry["sender_id"] == 7
        assert entry["sender_name"] == "Tom"
        assert entry["text"] == "hello room"
        assert entry["ts"].startswith("2026-08-07T12:00:00")

    def test_dm_and_group_share_the_same_code_path(self, scratch_project):
        assert shadow_append_inbox(
            scratch_project,
            chat_id=4242,
            message_id=1,
            sender_id=4242,
            sender_name="Tom",
            text="dm",
            date=datetime.now(UTC),
        )
        assert shadow_append_inbox(
            scratch_project,
            chat_id=-1004242,
            message_id=2,
            sender_id=7,
            sender_name="Tom",
            text="group",
            date=datetime.now(UTC),
        )
        rooms = Room.query.filter(project_key=scratch_project["_key"])
        assert len(rooms) == 2

    def test_never_raises_on_bad_project(self):
        assert (
            shadow_append_inbox(
                None,
                chat_id=1,
                message_id=1,
                sender_id=1,
                sender_name="x",
                text="x",
                date=datetime.now(UTC),
            )
            is False
        )
        assert (
            shadow_append_inbox(
                {},
                chat_id=1,
                message_id=1,
                sender_id=1,
                sender_name="x",
                text="x",
                date=datetime.now(UTC),
            )
            is False
        )

    def test_never_raises_on_resolve_failure(self, scratch_project, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("redis down")

        monkeypatch.setattr(Room, "resolve", classmethod(lambda cls, *a, **k: _boom()))
        ok = shadow_append_inbox(
            scratch_project,
            chat_id=1,
            message_id=1,
            sender_id=1,
            sender_name="x",
            text="x",
            date=datetime.now(UTC),
        )
        assert ok is False


class TestIntakeWiring:
    def test_live_intake_calls_the_shadow_append(self):
        """No correct-logic-dead-caller: the bridge intake actually invokes
        the shadow append (the notify_sdk_started failure shape this plan
        exists to prevent)."""
        import pathlib

        src = (
            pathlib.Path(__file__).resolve().parents[2] / "bridge" / "telegram_bridge.py"
        ).read_text(encoding="utf-8")
        assert "shadow_append_inbox(" in src
