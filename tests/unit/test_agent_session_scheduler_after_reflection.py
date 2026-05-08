"""#1342 Tier 3B item 3 — agent_session_scheduler --after creates a Reflection.

When ``cmd_schedule`` runs with ``--after <ISO>``, alongside writing the
``AgentSession`` (existing behavior, untouched), it now also creates a
``Reflection`` row with ``schedule="at:<ISO>"``, ``execution_type="agent"``,
``auto_delete_after_run=True`` so the scheduled work is visible on the
unified dashboard. This is purely additive: the public CLI signature is
unchanged, and the AgentSession dispatch path still drives execution. The
Reflection row is the registry surface only.

We don't exercise the actual GitHub fetch / AgentSession write here — those
are covered elsewhere. We assert exactly the new contract: the Reflection
record is written with the right shape after a successful schedule.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.unit]


def test_register_scheduled_reflection_writes_at_grammar(tmp_path, monkeypatch):
    """The helper writes a Reflection with the unified schedule grammar."""
    from tools.agent_session_scheduler import _register_scheduled_reflection

    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return MagicMock(name="reflection", reflection_id="r1")

    fake_query = MagicMock()
    fake_query.filter = MagicMock(return_value=iter([]))

    fake_reflection_cls = MagicMock()
    fake_reflection_cls.create = fake_create
    fake_reflection_cls.query = fake_query

    monkeypatch.setattr(
        "tools.agent_session_scheduler.Reflection",
        fake_reflection_cls,
        raising=False,
    )

    out = _register_scheduled_reflection(
        session_id="scheduled-113-abcd",
        scheduled_at=1_700_000_000.0,
        message_text="/sdlc https://example/issues/113\n\nIssue: Demo",
        priority="high",
        project_key="valor",
    )

    assert out is not None  # success
    assert captured["schedule"].startswith("at:")
    assert captured["execution_type"] == "agent"
    assert captured["auto_delete_after_run"] is True
    # The Reflection row name is namespaced so it doesn't collide with
    # registry-loaded entries.
    assert captured["name"].startswith("scheduled-")
    # The message body is preserved as the reflection's command for
    # operator visibility.
    assert "command" in captured
    assert "Demo" in captured["command"]


def test_register_scheduled_reflection_returns_none_on_failure(monkeypatch):
    """Reflection write failures are non-fatal — the AgentSession path wins."""
    from tools.agent_session_scheduler import _register_scheduled_reflection

    fake_query = MagicMock()
    fake_query.filter = MagicMock(return_value=iter([]))

    fake_reflection_cls = MagicMock()
    fake_reflection_cls.create = MagicMock(side_effect=RuntimeError("redis down"))
    fake_reflection_cls.query = fake_query

    monkeypatch.setattr(
        "tools.agent_session_scheduler.Reflection",
        fake_reflection_cls,
        raising=False,
    )

    out = _register_scheduled_reflection(
        session_id="scheduled-2",
        scheduled_at=1_700_000_000.0,
        message_text="x",
        priority="normal",
        project_key="valor",
    )
    assert out is None


def test_register_scheduled_reflection_skips_when_already_exists(monkeypatch):
    """Idempotent: re-running --after on the same scheduled_at doesn't double-create."""
    from tools.agent_session_scheduler import _register_scheduled_reflection

    existing = MagicMock(name="existing", reflection_id="r-existing")

    fake_query = MagicMock()
    fake_query.filter = MagicMock(return_value=iter([existing]))

    create_calls: list = []

    fake_reflection_cls = MagicMock()
    fake_reflection_cls.create = MagicMock(side_effect=lambda **kw: create_calls.append(kw))
    fake_reflection_cls.query = fake_query

    monkeypatch.setattr(
        "tools.agent_session_scheduler.Reflection",
        fake_reflection_cls,
        raising=False,
    )

    out = _register_scheduled_reflection(
        session_id="scheduled-3",
        scheduled_at=1_700_000_000.0,
        message_text="x",
        priority="normal",
        project_key="valor",
    )
    assert out is existing
    assert create_calls == []  # no double-create
