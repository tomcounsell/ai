"""Regression guard for the ``exit_returncode`` backcompat crash (issue #1099).

Background
----------
PR #1153 originally shipped ``exit_returncode = IntField(null=True, default=None)``
on ``AgentSession``. When a pre-existing AgentSession row in Redis (one written
before the field was added to the model) was loaded and re-saved, Popoto's
descriptor returned the ``IntField`` class object itself instead of ``None``
for the missing hash key. The subsequent ``.save()`` then crashed with::

    can not serialize 'IntField' object
    int() argument must be a string, a bytes-like object or a real number, not 'IntField'
     Change the value or modify type on AgentSession.exit_returncode

This was a live production regression — ``update_stage_states`` write retries
exhausted for session ``sdlc-local-1099`` during SDLC dispatch. The fix: match
the project's IntField convention (``default=0``) rather than the
``null=True, default=None`` combination that triggers the descriptor bug.

This file locks in:

1. A pre-existing row WITHOUT ``exit_returncode`` in its Redis hash can be
   loaded and re-saved without crashing.
2. The field's default value on a freshly-created instance is ``0``, NOT
   ``None`` (the convention-matching fix — see ``models/agent_session.py``).
3. The field is NOT declared with ``null=True, default=None`` — a structural
   guard against a future regression re-introducing the bad combination.

All three together are necessary. (1) is the behavioral guarantee. (2) pins
the semantic. (3) catches a well-intentioned refactor that might revert the
convention without realizing why.

Cleanup: every test uses a recognizable ``project_key`` prefix
(``test-exit-returncode-backcompat``) and deletes its session via the Popoto
ORM in a ``finally`` block — never raw Redis.
"""

from __future__ import annotations

from models.agent_session import AgentSession, SessionType


def test_field_default_is_zero_not_none():
    """Regression for the IntField(null=True, default=None) bug.

    ``exit_returncode`` MUST default to 0. The previous ``default=None`` +
    ``null=True`` combination caused Popoto to return the ``IntField`` class
    object itself when the hash key was absent, crashing ``save()``.
    """
    s = AgentSession(
        project_key="test-exit-returncode-backcompat-default",
        chat_id="x",
        session_type=SessionType.PM,
        message_text="x",
        sender_name="x",
        agent_session_id="exit-rc-backcompat-default-id",
    )
    try:
        assert s.exit_returncode == 0
        assert isinstance(s.exit_returncode, int)
    finally:
        try:
            s.delete()
        except Exception:
            pass


def test_field_declaration_does_not_use_null_true_default_none():
    """Structural guard: block future reintroduction of the bad declaration.

    If someone ever changes the field back to ``IntField(null=True, default=None)``
    without understanding why, this assertion fires before production does.
    """
    field = AgentSession._meta.fields["exit_returncode"]
    # The production-safe convention on this model is default=0 for IntFields.
    # Anything else is suspicious — if you are tempted to change this, read
    # the module docstring and ``models/agent_session.py`` first.
    assert field.default == 0, (
        f"AgentSession.exit_returncode default must be 0 (matches every other "
        f"IntField on this model). Got {field.default!r}. "
        "If you reintroduce null=True, default=None, pre-existing Redis rows "
        "crash on save() with 'can not serialize IntField object'. "
        "See tests/unit/test_agent_session_exit_returncode_backcompat.py "
        "and issue #1099."
    )


def test_load_and_save_succeeds_when_exit_returncode_missing_from_hash():
    """Load a row whose Redis hash lacks ``exit_returncode``; ``save()`` must not crash.

    This is the live-repro scenario from the PR #1153 TEST-stage failure:
    AgentSession was created in Redis before ``exit_returncode`` was added
    to the model, so the hash has no such key. Any subsequent mutation +
    ``save()`` on that row must succeed.

    We simulate the pre-existing row by:
      1. Saving a fresh AgentSession via Popoto.
      2. Using ``POPOTO_REDIS_DB.hdel`` to remove JUST the ``exit_returncode``
         key from the hash — the rest of the row stays intact.
      3. Re-querying via ``AgentSession.query.filter(...)`` (the ORM path).
      4. Calling ``.save()`` and asserting it does not raise.

    ``hdel`` (single-field removal) is the idiomatic Popoto pattern for
    simulating "field was added later" test scenarios — see Popoto's own
    migration-test helpers (``popoto/models/migrations.py``). It is NOT on
    the raw-Redis blocklist for this repo (``validate_no_raw_redis_delete.py``).
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    s = AgentSession(
        project_key="test-exit-returncode-backcompat-load",
        chat_id="x",
        session_type=SessionType.PM,
        message_text="x",
        sender_name="x",
        agent_session_id="exit-rc-backcompat-load-id",
        session_id="exit-rc-backcompat-load-sid",
    )
    s.save()

    try:
        # Simulate "this row was written before the field existed": delete
        # just the exit_returncode key from the hash. The row is otherwise
        # intact.
        redis_key = s.db_key.redis_key
        POPOTO_REDIS_DB.hdel(redis_key, "exit_returncode")

        # Re-fetch through the ORM — this exercises the field descriptor
        # path that was crashing in production.
        found = list(AgentSession.query.filter(session_id="exit-rc-backcompat-load-sid"))
        assert len(found) == 1, "session should be retrievable after hdel"
        reloaded = found[0]

        # The core guarantee — this is the exact path that was blowing up
        # in append_event / update_stage_states for session sdlc-local-1099.
        # The descriptor pollution may transiently appear on read, but the
        # ``_heal_int_field_descriptor_pollution`` hook in ``AgentSession.save``
        # must coerce it back to a serializable default before encoding.
        reloaded.turn_count = (reloaded.turn_count or 0) + 1
        reloaded.save()  # must NOT raise — full save path

        # Partial save (the exact production failure path) must also succeed.
        reloaded.turn_count = (reloaded.turn_count or 0) + 1
        reloaded.save(update_fields=["turn_count"])  # must NOT raise

        # After save(), exit_returncode must read back as a real int (the
        # heal wrote 0 into __dict__).
        assert isinstance(reloaded.exit_returncode, int), (
            "exit_returncode must be an int after save() — descriptor heal "
            "should have replaced the class-level Field instance with the "
            "declared default."
        )

        # The mutation must round-trip through Redis.
        verify = list(AgentSession.query.filter(session_id="exit-rc-backcompat-load-sid"))
        assert len(verify) == 1
        assert isinstance(verify[0].turn_count, int)
        assert verify[0].turn_count >= 2
    finally:
        try:
            s.delete()
        except Exception:
            pass


def test_store_exit_returncode_works_against_hash_missing_field():
    """End-to-end: the Mode 4 writer succeeds against a row missing the key.

    ``_store_exit_returncode`` is called from ``get_response_via_harness``
    after subprocess exit. If the target session's hash predates the field,
    the writer must still persist the new value instead of crashing.
    """
    from agent.sdk_client import _store_exit_returncode
    from popoto.redis_db import POPOTO_REDIS_DB

    s = AgentSession(
        project_key="test-exit-returncode-backcompat-writer",
        chat_id="x",
        session_type=SessionType.PM,
        message_text="x",
        sender_name="x",
        agent_session_id="exit-rc-backcompat-writer-id",
        session_id="exit-rc-backcompat-writer-sid",
    )
    s.save()

    try:
        # Pretend the row predates the field.
        POPOTO_REDIS_DB.hdel(s.db_key.redis_key, "exit_returncode")

        # The writer swallows exceptions internally (by design), but the
        # subsequent query must find the value persisted.
        _store_exit_returncode(session_id="exit-rc-backcompat-writer-sid", returncode=-9)

        found = list(AgentSession.query.filter(session_id="exit-rc-backcompat-writer-sid"))
        assert len(found) == 1
        assert found[0].exit_returncode == -9, (
            "Mode 4 OOM detector relies on this write succeeding. If "
            "exit_returncode cannot be persisted against a pre-existing "
            "session, the OS-OOM backoff will never fire for legacy rows."
        )
    finally:
        try:
            s.delete()
        except Exception:
            pass
