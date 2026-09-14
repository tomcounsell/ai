"""Unit tests for reflections/audits/redis_quality_audit.py (#3181).

Covers the dead-branch removal in the dead-channel scan: `Chat.updated_at` is
`SortedField(type=float)` (models/chat.py:23), never a datetime, so the
`isinstance(_ua, datetime)` branch that used to sit in front of a
naive-tzinfo coercion was unreachable by construction -- line 55 of the audit
already compares `chat.updated_at` against the float `month_ago`. These tests
prove the field-type claim the dead-branch verdict rests on.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.reflections]


class TestChatUpdatedAtIsFloat:
    """`Chat.updated_at` round-trips as a float through popoto."""

    def test_updated_at_is_float_after_round_trip(self, redis_test_db):
        """Fixture built by writing through popoto and reading back."""
        from models.chat import Chat

        uid = uuid.uuid4().hex[:8]
        chat = Chat(
            chat_id=f"quality-audit-test-{uid}",
            chat_name="quality-audit-test-chat",
            updated_at=time.time(),
        )
        chat.save()
        try:
            reloaded = Chat.query.filter(chat_id=chat.chat_id).first()
            assert reloaded is not None
            assert isinstance(reloaded.updated_at, float), (
                "Chat.updated_at must round-trip as a float, got "
                f"{type(reloaded.updated_at).__name__}"
            )
        finally:
            chat.delete()

    def test_a_datetime_cannot_be_saved_into_updated_at(self, redis_test_db):
        """The falsifier of the deadness claim: a datetime CANNOT live in
        this field at all -- the stronger form of the dead-code verdict.
        `isinstance(_ua, datetime)` was unreachable by construction, not
        merely unobserved in practice."""
        from popoto.exceptions import ModelException

        from models.chat import Chat

        uid = uuid.uuid4().hex[:8]
        chat = Chat(
            chat_id=f"quality-audit-test-dt-{uid}",
            chat_name="quality-audit-test-chat-dt",
            updated_at=time.time(),
        )
        chat.save()
        try:
            chat.updated_at = datetime.now(UTC)
            with pytest.raises(ModelException):
                chat.save()
        finally:
            chat.delete()
