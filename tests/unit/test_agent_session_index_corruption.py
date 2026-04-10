"""Tests for AgentSession status index corruption fixes.

Validates that status transitions on lazy-loaded sessions correctly remove the
old index entry from Redis, preventing ghost "running" entries.

Root cause (Bug 1): Popoto's _create_lazy_model() initialises _saved_field_values
with only KeyFields populated. IndexedFieldMixin.on_save() skips the srem() call
when the old value is not present in _saved_field_values. The fix backfills
_saved_field_values["status"] in both transition_status() and finalize_session()
before calling session.save().
"""

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lazy_session(status="running"):
    """Simulate a lazy-loaded AgentSession with minimal _saved_field_values.

    Popoto's _create_lazy_model seeds _saved_field_values with KeyFields only
    (e.g., agent_session_id). Status is an IndexedField and is NOT in
    _saved_field_values after lazy load.
    """
    session = MagicMock()
    session.status = status
    session.session_id = "sess-lazy-001"
    session.agent_session_id = "ase-lazy-001"
    session.parent_agent_session_id = None
    # Simulate lazy-loaded state: only key field is seeded, not status
    session._saved_field_values = {"agent_session_id": "ase-lazy-001"}
    return session


def _make_fully_loaded_session(status="running"):
    """Simulate a fully-loaded AgentSession with all field values populated.

    This represents a session that was saved/loaded normally — _saved_field_values
    includes the current status, so on_save() would already work correctly.
    """
    session = MagicMock()
    session.status = status
    session.session_id = "sess-full-001"
    session.agent_session_id = "ase-full-001"
    session.parent_agent_session_id = None
    # Fully-loaded: _saved_field_values includes status
    session._saved_field_values = {"agent_session_id": "ase-full-001", "status": status}
    return session


# ---------------------------------------------------------------------------
# transition_status — lazy-load backfill
# ---------------------------------------------------------------------------


class TestTransitionStatusLazyLoadBackfill:
    def test_backfills_saved_field_values_on_lazy_session(self):
        """transition_status() must set _saved_field_values['status'] before save().

        This is the core Bug 1 fix: ensures Popoto's on_save() guard can remove
        the old index entry even when the session was lazy-loaded.
        """
        from models.session_lifecycle import transition_status

        session = _make_lazy_session(status="running")

        transition_status(session, "dormant")

        # _saved_field_values["status"] must be set to the OLD status before save
        assert session._saved_field_values["status"] == "running"
        session.save.assert_called_once()

    def test_backfills_even_when_current_status_is_pending(self):
        """Backfill works for pending -> running transition too."""
        from models.session_lifecycle import transition_status

        session = _make_lazy_session(status="pending")

        transition_status(session, "running")

        assert session._saved_field_values["status"] == "pending"
        session.save.assert_called_once()

    def test_does_not_crash_when_no_saved_field_values_attr(self):
        """If session has no _saved_field_values attribute, backfill is skipped silently."""
        from models.session_lifecycle import transition_status

        session = MagicMock()
        session.status = "pending"
        session.session_id = "sess-no-attr"
        session.agent_session_id = "ase-no-attr"
        session.parent_agent_session_id = None
        # Simulate session object without _saved_field_values (e.g., future Popoto change)
        del session._saved_field_values

        # Should not raise
        transition_status(session, "running")
        session.save.assert_called_once()

    def test_fully_loaded_session_backfill_is_still_correct(self):
        """For fully-loaded sessions, backfill sets _saved_field_values to old status.

        Overwriting with the same value is harmless — it's idempotent.
        """
        from models.session_lifecycle import transition_status

        session = _make_fully_loaded_session(status="pending")

        transition_status(session, "running")

        assert session._saved_field_values["status"] == "pending"
        session.save.assert_called_once()

    def test_idempotent_saves_companion_fields_when_already_in_target_status(self):
        """transition_status() saves companion fields even when already in the target state.

        Changed in #875: the idempotent path now calls save() to persist any
        companion fields the caller may have set before calling transition_status().
        """
        from models.session_lifecycle import transition_status

        session = _make_lazy_session(status="running")

        transition_status(session, "running")

        session.save.assert_called_once()


# ---------------------------------------------------------------------------
# finalize_session — lazy-load backfill
# ---------------------------------------------------------------------------


class TestFinalizeSessionLazyLoadBackfill:
    def test_backfills_saved_field_values_on_lazy_session(self):
        """finalize_session() must set _saved_field_values['status'] before save().

        Both transition_status() and finalize_session() use session.save(), so
        both need the backfill to ensure the old index entry is removed.
        """
        from models.session_lifecycle import finalize_session

        session = _make_lazy_session(status="running")

        with (
            patch.object(session, "log_lifecycle_transition"),
            patch("models.session_lifecycle._finalize_parent_sync"),
        ):
            finalize_session(
                session,
                "killed",
                reason="CLI kill",
                skip_auto_tag=True,
                skip_checkpoint=True,
                skip_parent=True,
            )

        assert session._saved_field_values["status"] == "running"
        session.save.assert_called_once()

    def test_backfills_for_abandoned_transition(self):
        """Backfill works when finalizing as abandoned."""
        from models.session_lifecycle import finalize_session

        session = _make_lazy_session(status="running")

        with patch.object(session, "log_lifecycle_transition"):
            finalize_session(
                session,
                "abandoned",
                skip_auto_tag=True,
                skip_checkpoint=True,
                skip_parent=True,
            )

        assert session._saved_field_values["status"] == "running"
        session.save.assert_called_once()

    def test_idempotent_noop_when_already_in_terminal_state(self):
        """finalize_session() is a no-op when session is already in that terminal state."""
        from models.session_lifecycle import finalize_session

        session = _make_lazy_session(status="killed")

        finalize_session(session, "killed", skip_auto_tag=True, skip_checkpoint=True)

        session.save.assert_not_called()

    def test_does_not_crash_when_no_saved_field_values_attr(self):
        """If session has no _saved_field_values attribute, backfill is skipped silently."""
        from models.session_lifecycle import finalize_session

        session = MagicMock()
        session.status = "running"
        session.session_id = "sess-no-attr"
        session.agent_session_id = "ase-no-attr"
        session.parent_agent_session_id = None
        del session._saved_field_values

        finalize_session(
            session,
            "killed",
            skip_auto_tag=True,
            skip_checkpoint=True,
            skip_parent=True,
        )
        session.save.assert_called_once()
