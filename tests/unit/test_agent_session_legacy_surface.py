"""Issue #2873: the removed ``AgentSession`` legacy surface stays removed.

Two things are pinned here:

1. The deprecated aliases and zero-caller wrappers no longer exist on the model.
2. ``_normalize_kwargs`` no longer rewrites legacy field names. The
   ``commit_sha``/``summary`` paths in particular were unguarded and appended an
   event to ``session_events`` on *every* hydration of a row that carried both a
   live ``session_events`` key and a stale legacy key -- unbounded growth on a
   read path.
"""

import pytest

from models.agent_session import AgentSession


class TestRemovedAliases:
    """Deprecated aliases and zero-caller wrappers are gone."""

    @pytest.mark.parametrize(
        "name",
        [
            "get_parent_chat_session",
            "get_dev_sessions",
            "get_history_list",
            "_get_history_list",
            "sender",
            "history",
        ],
    )
    def test_attribute_absent(self, name):
        assert not hasattr(AgentSession, name), f"AgentSession.{name} should have been removed"

    @pytest.mark.parametrize(
        "name",
        ["get_parent_session", "get_child_sessions", "sender_name", "session_events"],
    )
    def test_canonical_replacement_present(self, name):
        assert hasattr(AgentSession, name)


class TestNormalizeKwargsLegacyPathsRemoved:
    """Legacy field names are no longer mapped onto canonical fields."""

    @pytest.mark.parametrize(
        ("legacy_key", "legacy_value", "canonical_key"),
        [
            ("work_item_slug", "some-slug", "slug"),
            ("last_activity", 1_700_000_000, "updated_at"),
            ("scheduled_after", 1_700_000_000, "scheduled_at"),
            ("parent_job_id", "parent-123", "parent_agent_session_id"),
            ("history", ["old entry"], "session_events"),
        ],
    )
    def test_legacy_key_is_not_remapped(self, legacy_key, legacy_value, canonical_key):
        result = AgentSession._normalize_kwargs({legacy_key: legacy_value})
        assert canonical_key not in result

    @pytest.mark.parametrize(
        "legacy_key",
        [
            "job_id",
            "depends_on",
            "stable_agent_session_id",
            "scheduling_depth",
            "_qa_mode_legacy",
            "stage_states",
            "commit_sha",
            "summary",
        ],
    )
    def test_legacy_key_does_not_crash_normalization(self, legacy_key):
        """Unknown keys survive normalization untouched; Popoto never persists them."""
        result = AgentSession._normalize_kwargs({legacy_key: "legacy-value"})
        assert result[legacy_key] == "legacy-value"

    @pytest.mark.parametrize("legacy_key", ["commit_sha", "summary"])
    def test_no_duplicate_event_growth_on_hydration(self, legacy_key):
        """A row with live ``session_events`` plus a stale legacy key does not grow.

        This was the unguarded-append bug: ``commit_sha`` and ``summary`` were the
        only two paths lacking the ``"session_events" not in kwargs`` guard.
        """
        live_events = [{"event_type": "lifecycle", "text": "pending->running"}]
        kwargs = {"session_events": list(live_events), legacy_key: "abc123"}

        result = AgentSession._normalize_kwargs(kwargs)

        assert result["session_events"] == live_events
