"""Tests for ``models.session_event.format_event_lines``.

The helper replaces the removed ``AgentSession.get_history_list()`` /
``_get_history_list`` methods (issue #2873). Three production call sites depend
on the exact ``"[{event_type}] {text}"`` shape, so it is pinned here.
"""

import pytest

from models.session_event import SessionEvent, format_event_lines


class TestFormatEventLines:
    """The formatting contract inherited from the removed model method."""

    @pytest.mark.parametrize(
        ("events", "expected"),
        [
            pytest.param(None, [], id="none"),
            pytest.param([], [], id="empty"),
            pytest.param("not-a-list", [], id="non-list"),
            pytest.param({"event_type": "stage"}, [], id="bare-dict-not-a-list"),
            pytest.param(
                [{"event_type": "stage", "text": "test=completed"}],
                ["[stage] test=completed"],
                id="dict-event",
            ),
            pytest.param(
                [{"text": "no type"}],
                ["[system] no type"],
                id="dict-missing-event-type-defaults-to-system",
            ),
            pytest.param(
                [{"event_type": "summary"}],
                ["[summary] "],
                id="dict-missing-text-is-empty",
            ),
            pytest.param(
                ["legacy flat string"],
                ["legacy flat string"],
                id="str-event-passes-through",
            ),
            pytest.param([12345, None], [], id="unsupported-entries-skipped"),
            pytest.param(
                [{"event_type": "stage", "text": "a"}, "b", 7],
                ["[stage] a", "b"],
                id="mixed-entries",
            ),
        ],
    )
    def test_formatting(self, events, expected):
        assert format_event_lines(events) == expected

    def test_accepts_serialized_session_event(self):
        """A real ``SessionEvent.model_dump()`` round-trips into the pinned shape."""
        event = SessionEvent.stage_change("test", "completed")
        assert format_event_lines([event.model_dump()]) == ["[stage] test=completed"]

    def test_goal_gate_match_shape(self):
        """``agent/goal_gates.py`` matches on ``[stage]``/``test``/``completed``."""
        line = format_event_lines([SessionEvent.stage_change("test", "completed").model_dump()])[0]
        lowered = line.lower()
        assert "[stage]" in lowered
        assert "test" in lowered
        assert "completed" in lowered
