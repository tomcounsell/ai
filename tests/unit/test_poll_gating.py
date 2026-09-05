"""Poll eligibility (#2701, Task 3).

Every branch here is a fail-closed decision. The asymmetry that justifies it: a
question rendered as prose to an eng session is a cosmetic loss, while a poll
rendered into a teammate chat is a scope violation, and a poll into a DM is a
hard MTProto rejection that consumes retries while an agent stays blocked.
"""

from unittest.mock import MagicMock, patch

import pytest

from bridge.poll_gating import poll_eligible
from bridge.read_the_room import is_group_chat
from tests.unit.session_lookup_mock import wire_session_lookup


class TestIsGroupChatHasExactlyOneHome:
    def test_the_public_name_lives_in_read_the_room(self):
        """One definition, no back-compat alias.

        A second copy would silently drift from the RTR suppression rule, and an
        alias would satisfy a `def`-counting grep while keeping two live names.
        """
        import bridge.poll_gating as gating
        import bridge.read_the_room as rtr

        assert callable(rtr.is_group_chat)
        assert not hasattr(rtr, "_is_group_chat"), "back-compat alias must not exist"
        # poll_gating imports it rather than owning it — the dependency arrow
        # points from the feature module at the generic predicate.
        assert gating.is_group_chat is rtr.is_group_chat

    def test_read_the_room_does_not_import_from_the_poll_module(self):
        """The naming inversion that later invites a second copy."""
        import inspect

        import bridge.read_the_room as rtr

        assert "poll_gating" not in inspect.getsource(rtr)

    @pytest.mark.parametrize(
        "chat_id,expected",
        [(-1003449100931, True), (179144806, False), (0, False), (None, False), ("junk", False)],
    )
    def test_sign_discriminator_semantics_unchanged(self, chat_id, expected):
        assert is_group_chat(chat_id) is expected


def _session(session_type):
    s = MagicMock()
    s.session_type = session_type
    s.created_at = 1
    return s


class TestPollEligible:
    def test_group_plus_eng_is_eligible(self):
        with patch("models.agent_session.AgentSession") as model:
            model.query.filter.return_value = [_session("eng")]
            wire_session_lookup(model)
            result = poll_eligible(-1003449100931, "sess-1")
        assert result.ok
        assert result.reason == "eligible"

    @pytest.mark.parametrize("chat_id", [179144806, 0, None, "junk", ""])
    def test_non_group_is_not_a_group(self, chat_id):
        result = poll_eligible(chat_id, "sess-1")
        assert not result.ok
        assert result.reason == "not_a_group"

    def test_teammate_session_is_not_eng(self):
        with patch("models.agent_session.AgentSession") as model:
            model.query.filter.return_value = [_session("teammate")]
            wire_session_lookup(model)
            result = poll_eligible(-1003449100931, "sess-1")
        assert not result.ok
        assert result.reason == "not_eng_session"

    def test_missing_record_is_ineligible(self):
        with patch("models.agent_session.AgentSession") as model:
            model.query.filter.return_value = []
            wire_session_lookup(model)
            result = poll_eligible(-1003449100931, "sess-1")
        assert not result.ok
        assert result.reason == "unknown_session_type"

    def test_null_session_type_is_ineligible(self):
        """session_type is null=True on the model, so this is reachable."""
        with patch("models.agent_session.AgentSession") as model:
            model.query.filter.return_value = [_session(None)]
            wire_session_lookup(model)
            result = poll_eligible(-1003449100931, "sess-1")
        assert not result.ok
        assert result.reason == "unknown_session_type"

    def test_unrecognized_session_type_is_ineligible(self):
        with patch("models.agent_session.AgentSession") as model:
            model.query.filter.return_value = [_session("something-new")]
            wire_session_lookup(model)
            result = poll_eligible(-1003449100931, "sess-1")
        assert not result.ok
        assert result.reason == "unknown_session_type"

    def test_missing_session_id_is_ineligible(self):
        assert poll_eligible(-1003449100931, None).reason == "unknown_session_type"

    def test_exception_never_raises_and_returns_eligibility_error(self):
        """A delivery seam must not throw."""
        with patch("models.agent_session.AgentSession") as model:
            model.query.filter.side_effect = RuntimeError("redis down")
            wire_session_lookup(model)
            result = poll_eligible(-1003449100931, "sess-1")
        assert not result.ok
        assert result.reason == "eligibility_error"

    def test_newest_record_decides(self):
        """A stale duplicate row must not decide a live session's eligibility."""
        old = _session("teammate")
        old.created_at = 1
        new = _session("eng")
        new.created_at = 99
        with patch("models.agent_session.AgentSession") as model:
            model.query.filter.return_value = [old, new]
            wire_session_lookup(model)
            assert poll_eligible(-1003449100931, "sess-1").ok

    def test_gate_compares_the_enum_not_a_bare_literal(self):
        """Two string literals in two places is how a discriminator drifts."""
        import inspect

        import bridge.poll_gating as gating

        source = inspect.getsource(gating)
        assert "SessionType.ENG" in source
        assert 'session_type == "eng"' not in source
