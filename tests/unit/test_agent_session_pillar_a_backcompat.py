"""Regression guard for the Pillar A liveness-fields backcompat crash (issue #1172).

Background
----------
PR #1177 added five new nullable fields to ``AgentSession`` for in-flight
visibility (issue #1172, Pillar A):

  * ``current_tool_name``       (Field,         null=True, default=None)
  * ``last_tool_use_at``        (DatetimeField, null=True)
  * ``last_turn_at``             (DatetimeField, null=True)
  * ``recent_thinking_excerpt`` (Field,         null=True, default=None)
  * ``self_report_sent_at``      (DatetimeField, null=True)

When a pre-existing AgentSession row in Redis (one written before PR #1177
landed) was loaded via ``decode_popoto_model_hashmap``, the missing hash
keys leaked the class-level ``DatetimeField`` / ``Field`` descriptors
through to attribute access. Any subsequent ``save()`` then crashed in
Popoto's ``pre_save_format`` (``popoto/models/base.py:818``) with::

    'DatetimeField' object cannot be interpreted as an integer
     Change the value or modify type on AgentSession.last_tool_use_at

This was a live production regression. Two repros:

  1. Worker task crash 2026-04-27T17:49:31Z (session
     ``tg_valor_-1003879986445_493``): ``_worker_loop`` raised
     ``ModelException('Model instance parameters invalid. Failed to save.')``
     unhandled.
  2. Lost PM message 2026-04-28T01:00:20Z (session
     ``tg_valor_-1003449100931_731``, message "use do-isssue to describe
     the bug..."): the agent-session-cleanup reaper marked the session
     "Unsaveable" 11 minutes after enqueue, the dashboard's phantom-filter
     hid it, and the message was silently lost.

PR #1153 / issue #1099 had already shipped a heal mechanism for the
``IntField`` flavour of this same bug (``_heal_int_field_descriptor_pollution``
+ ``_INT_FIELDS_BACKCOMPAT``), but it was IntField-specific. PR #1177
proved that maintaining a per-type backcompat list does not scale — the
new DatetimeField / Field additions inherited none of the protection.

The fix generalized the heal to walk every model field (now
``_heal_descriptor_pollution``), so future field additions automatically
inherit the protection. This file pins the new behaviour for the five
Pillar A fields specifically, plus a structural test that the heal is
generic (not per-type).

Cleanup: every test uses a recognizable ``project_key`` prefix
(``test-pillar-a-backcompat``) and deletes its session via the Popoto ORM
in a ``finally`` block — never raw Redis.
"""

from __future__ import annotations

from datetime import UTC, datetime

from models.agent_session import AgentSession, SessionType

PILLAR_A_FIELDS = (
    "current_tool_name",
    "last_tool_use_at",
    "last_turn_at",
    "recent_thinking_excerpt",
    "self_report_sent_at",
)


def test_pillar_a_fields_default_to_none_on_fresh_instance():
    """Fresh-instance defaults for the five Pillar A fields must all be ``None``.

    All five are declared ``null=True`` (no value yet recorded). Defaults
    other than ``None`` would mean every new session reports a fictitious
    tool/turn timestamp, breaking the dashboard liveness signal.
    """
    s = AgentSession(
        project_key="test-pillar-a-backcompat-default",
        chat_id="x",
        session_type=SessionType.PM,
        message_text="x",
        sender_name="x",
        agent_session_id="pillar-a-default-id",
    )
    try:
        for field_name in PILLAR_A_FIELDS:
            value = getattr(s, field_name)
            assert value is None, (
                f"AgentSession.{field_name} default must be None on a fresh "
                f"instance. Got {value!r} (type={type(value).__name__})."
            )
    finally:
        try:
            s.delete()
        except Exception:
            pass


def test_load_and_save_succeeds_when_pillar_a_fields_missing_from_hash():
    """Load a row whose Redis hash lacks every Pillar A field; ``save()`` must not crash.

    This is the live-repro scenario from the 2026-04-27/28 production
    failures: AgentSession rows existed in Redis before PR #1177 added the
    Pillar A fields, so their hashes had no such keys. Any subsequent
    mutation + ``save()`` on those rows must succeed.

    We simulate the pre-existing rows by:
      1. Saving a fresh AgentSession via Popoto.
      2. Using ``POPOTO_REDIS_DB.hdel`` to remove every Pillar A key from
         the hash — the rest of the row stays intact.
      3. Re-querying via ``AgentSession.query.filter(...)`` (the ORM path).
      4. Calling ``.save()`` (both full and partial) and asserting it does
         not raise.

    ``hdel`` is the idiomatic Popoto pattern for simulating "field was
    added later" test scenarios — see Popoto's own migration-test helpers
    (``popoto/models/migrations.py``). It is NOT on the raw-Redis blocklist
    (``validate_no_raw_redis_delete.py``).
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    s = AgentSession(
        project_key="test-pillar-a-backcompat-load",
        chat_id="x",
        session_type=SessionType.PM,
        message_text="x",
        sender_name="x",
        agent_session_id="pillar-a-load-id",
        session_id="pillar-a-load-sid",
    )
    s.save()

    try:
        redis_key = s.db_key.redis_key
        for field_name in PILLAR_A_FIELDS:
            POPOTO_REDIS_DB.hdel(redis_key, field_name)

        # Re-fetch through the ORM — exercises the field descriptor path
        # that crashed in production.
        found = list(AgentSession.query.filter(session_id="pillar-a-load-sid"))
        assert len(found) == 1, "session should be retrievable after hdel"
        reloaded = found[0]

        # Partial save — the exact production failure path. Liveness writers
        # call this from PreToolUse/PostToolUse hooks. Must not raise.
        reloaded.last_tool_use_at = datetime.now(tz=UTC)
        reloaded.current_tool_name = "Bash"
        reloaded.save(update_fields=["last_tool_use_at", "current_tool_name"])

        # Full save — append_event / status transitions take this path.
        reloaded.turn_count = (reloaded.turn_count or 0) + 1
        reloaded.save()

        # After save(), every Pillar A field that the heal touched must
        # read back as a sane default (None) or the value we just wrote.
        # The descriptor must not leak through.
        for field_name in PILLAR_A_FIELDS:
            value = getattr(reloaded, field_name)
            field = AgentSession._meta.fields[field_name]
            assert value is not field, (
                f"AgentSession.{field_name} still holds the class descriptor "
                f"after save() — descriptor heal is not running. Got {value!r}."
            )

        # Mutation must round-trip through Redis.
        verify = list(AgentSession.query.filter(session_id="pillar-a-load-sid"))
        assert len(verify) == 1
        assert verify[0].current_tool_name == "Bash"
        assert isinstance(verify[0].last_tool_use_at, datetime)
        assert verify[0].turn_count >= 1
    finally:
        try:
            s.delete()
        except Exception:
            pass


def test_heal_is_generic_not_per_field_type():
    """Structural guard: the heal walks every model field, not a hand-curated list.

    PR #1177 demonstrated that a per-type list (``_INT_FIELDS_BACKCOMPAT``)
    fails to protect new fields — the IntField guard from #1153 didn't cover
    the DatetimeField additions. The fix generalized to walk every
    ``self._meta.fields`` entry, so future field additions are protected
    automatically.

    This test pins the generality. If someone reintroduces a per-type
    backcompat list, this assertion fires before production does.
    """
    import inspect

    src = inspect.getsource(AgentSession._heal_descriptor_pollution)
    # The heal must iterate `self._meta.fields` (every field), not a
    # type-specific subset like `self._INT_FIELDS_BACKCOMPAT`.
    assert "self._meta.fields" in src, (
        "_heal_descriptor_pollution must walk every model field via "
        "self._meta.fields. If you replace this with a per-type list, "
        "the next field addition will reintroduce the bug fixed by "
        "issue #1172. See the module docstring."
    )


def test_liveness_writer_save_succeeds_against_hash_missing_pillar_a_fields():
    """End-to-end: the liveness writer succeeds against a row missing the keys.

    ``agent.hooks.liveness_writers._save_tool_boundary`` is called from
    PreToolUse/PostToolUse hooks every tool boundary. If the target session's
    hash predates PR #1177, the write must persist instead of crashing.

    This is the exact path that produced the 2026-04-27T17:49:31Z worker
    task crash and the 2026-04-28T01:11:21Z PM-session-lost incident.
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    from agent.hooks.liveness_writers import _save_tool_boundary

    s = AgentSession(
        project_key="test-pillar-a-backcompat-writer",
        chat_id="x",
        session_type=SessionType.PM,
        message_text="x",
        sender_name="x",
        agent_session_id="pillar-a-writer-id",
        session_id="pillar-a-writer-sid",
    )
    s.save()

    try:
        for field_name in PILLAR_A_FIELDS:
            POPOTO_REDIS_DB.hdel(s.db_key.redis_key, field_name)

        # The writer returns False on internal failure (by design — hook
        # callers must never crash). True means save succeeded.
        ok = _save_tool_boundary(
            session_id="pillar-a-writer-sid",
            tool_name="Bash",
            ts=datetime.now(tz=UTC),
        )
        assert ok is True, (
            "Liveness writer must succeed against a pre-existing row whose "
            "hash predates the Pillar A fields. If this fails, every "
            "PreToolUse/PostToolUse hook on legacy sessions silently no-ops "
            "and the dashboard liveness signal goes dark."
        )

        found = list(AgentSession.query.filter(session_id="pillar-a-writer-sid"))
        assert len(found) == 1
        assert found[0].current_tool_name == "Bash"
        assert isinstance(found[0].last_tool_use_at, datetime)
    finally:
        try:
            s.delete()
        except Exception:
            pass
