"""Tests for agent/output_router.py — delivery action routing logic.

Covers the waiting_for_children guard (issue #1004) and general routing.
"""

import pytest

from agent.output_router import (
    MAX_NUDGE_COUNT,
    determine_delivery_action,
)

# Issue #1058: `PIPELINE_COMPLETE_MARKER` was removed. Tests now use the
# literal string to assert it is treated as ordinary content (no special
# routing).
_LEGACY_MARKER = "[PIPELINE_COMPLETE]"


class TestWaitingForChildrenGuard:
    """Issue #1004: PM in waiting_for_children must deliver, not nudge."""

    def test_pm_sdlc_waiting_for_children_delivers(self):
        """PM+SDLC session in waiting_for_children returns deliver, not nudge_continue."""
        action = determine_delivery_action(
            msg="Dispatched BUILD. Waiting for completion.",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status="waiting_for_children",
            session_type="eng",
            classification_type="sdlc",
        )
        assert action == "deliver"

    def test_pm_sdlc_running_still_nudges(self):
        """PM+SDLC session in running status still returns nudge_continue."""
        action = determine_delivery_action(
            msg="Working on the pipeline...",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status="running",
            session_type="eng",
            classification_type="sdlc",
        )
        assert action == "nudge_continue"

    def test_pm_sdlc_active_still_nudges(self):
        """PM+SDLC session in active status still returns nudge_continue."""
        action = determine_delivery_action(
            msg="Assessing pipeline state...",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status="active",
            session_type="eng",
            classification_type="sdlc",
        )
        assert action == "nudge_continue"

    def test_teammate_waiting_for_children_unaffected(self):
        """Teammate session in waiting_for_children delivers normally (not PM path)."""
        action = determine_delivery_action(
            msg="Some output from teammate.",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status="waiting_for_children",
            session_type="teammate",
            classification_type=None,
        )
        # Teammate sessions don't hit the PM+SDLC path, so they deliver normally
        assert action == "deliver"

    def test_waiting_for_children_with_none_session_type(self):
        """waiting_for_children guard should not trigger without session_type=pm."""
        action = determine_delivery_action(
            msg="Some output.",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status="waiting_for_children",
            session_type=None,
            classification_type=None,
        )
        assert action == "deliver"

    def test_pm_sdlc_waiting_for_children_with_legacy_marker_string(self):
        """Even with the legacy PIPELINE_COMPLETE string in the message,
        waiting_for_children guard takes precedence and delivers (issue #1058:
        the router no longer special-cases the string anywhere)."""
        action = determine_delivery_action(
            msg=f"Done. {_LEGACY_MARKER}",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status="waiting_for_children",
            session_type="eng",
            classification_type="sdlc",
        )
        # waiting_for_children guard fires before the PM+SDLC check
        assert action == "deliver"

    def test_pm_non_sdlc_waiting_for_children_delivers(self):
        """PM non-SDLC session in waiting_for_children still delivers."""
        action = determine_delivery_action(
            msg="Waiting for child.",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status="waiting_for_children",
            session_type="eng",
            classification_type="collaboration",
        )
        assert action == "deliver"

    @pytest.mark.parametrize("session_status", [None, "running", "active", "pending"])
    def test_non_waiting_statuses_do_not_trigger_guard(self, session_status):
        """Only waiting_for_children triggers the early deliver guard."""
        action = determine_delivery_action(
            msg="Pipeline work in progress.",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status=session_status,
            session_type="eng",
            classification_type="sdlc",
        )
        assert action == "nudge_continue"


class TestExistingRouting:
    """Ensure existing routing behavior is preserved."""

    def test_terminal_status_delivers_already_completed(self):
        action = determine_delivery_action(
            msg="final output",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status="completed",
        )
        assert action == "deliver_already_completed"

    def test_completion_sent_drops(self):
        action = determine_delivery_action(
            msg="more output",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            completion_sent=True,
        )
        assert action == "drop"

    def test_pm_sdlc_legacy_marker_string_is_ordinary_content(self):
        """Issue #1058: the legacy `[PIPELINE_COMPLETE]` string is no longer
        content-inspected. It routes identically to any other PM+SDLC output —
        i.e., nudge_continue to keep the pipeline moving."""
        action = determine_delivery_action(
            msg=f"All done! {_LEGACY_MARKER}",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_type="eng",
            classification_type="sdlc",
        )
        assert action == "nudge_continue"

    def test_pm_sdlc_normal_nudges(self):
        action = determine_delivery_action(
            msg="Working on BUILD stage...",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_type="eng",
            classification_type="sdlc",
        )
        assert action == "nudge_continue"

    def test_rate_limited_nudges(self):
        action = determine_delivery_action(
            msg="partial",
            stop_reason="rate_limited",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
        )
        assert action == "nudge_rate_limited"

    def test_empty_output_nudges(self):
        action = determine_delivery_action(
            msg="",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
        )
        assert action == "nudge_empty"

    def test_normal_end_turn_delivers(self):
        action = determine_delivery_action(
            msg="Here is the answer.",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
        )
        assert action == "deliver"


class TestOpenQuestionPause:
    """Issue #2701: a session that has already asked the human must wait.

    Guards a defect wider than the poll feature. The eng+sdlc ``nudge_continue``
    line is unconditional and sits ahead of every ``stop_reason`` branch, so an
    sdlc eng session that poses a question — as a Telegram poll *or* as plain
    prose — is auto-nudged past it and proceeds on a guess. Owner decision 7b
    (see ``docs/plans/ask-me-telegram-polls.md``).
    """

    @staticmethod
    def _sdlc(**overrides):
        kwargs = {
            "msg": "Which approach should I take?",
            "stop_reason": "end_turn",
            "auto_continue_count": 0,
            "max_nudge_count": MAX_NUDGE_COUNT,
            "session_type": "eng",
            "classification_type": "sdlc",
        }
        kwargs.update(overrides)
        return determine_delivery_action(**kwargs)

    def test_open_question_pauses_instead_of_nudging(self):
        assert self._sdlc(has_open_question=True) == "pause_open_question"

    def test_no_open_question_still_nudges_continue(self):
        """The blast-radius assertion: the default leaves the nudge loop alone."""
        assert self._sdlc(has_open_question=False) == "nudge_continue"

    def test_default_is_false(self):
        """Omitting the keyword entirely must behave exactly as before."""
        assert self._sdlc() == "nudge_continue"

    def test_non_sdlc_session_unaffected_by_default(self):
        action = determine_delivery_action(
            msg="Here is the answer.",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
        )
        assert action == "deliver"

    # --- Placement: every earlier guard still wins over the new branch -------
    # The pause branch sits after the terminal / completion-sent / compaction /
    # watchdog / rate-limit / empty-output / cap guards and before the eng+sdlc
    # line. A session that is dying, wedged, rate-limited or capped must take
    # its own path even while holding an open question.

    @pytest.mark.parametrize(
        "overrides,expected",
        [
            ({"session_status": "completed"}, "deliver_already_completed"),
            ({"completion_sent": True}, "drop"),
            ({"watchdog_unhealthy": "stuck"}, "deliver"),
            ({"stop_reason": "rate_limited"}, "nudge_rate_limited"),
            ({"msg": ""}, "nudge_empty"),
            ({"auto_continue_count": MAX_NUDGE_COUNT}, "deliver"),
        ],
    )
    def test_earlier_guards_win_over_pause(self, overrides, expected):
        assert self._sdlc(has_open_question=True, **overrides) == expected

    def test_post_compaction_guard_wins_over_pause(self):
        import time

        action = self._sdlc(has_open_question=True, last_compaction_ts=time.time())
        assert action == "defer_post_compact"

    def test_waiting_for_children_wins_over_pause(self):
        """#1004's semaphore release must not be blocked by an open question."""
        action = self._sdlc(has_open_question=True, session_status="waiting_for_children")
        assert action == "deliver"

    def test_router_performs_no_io(self):
        """The decision function stays pure — the caller does the registry read.

        Checked over the AST rather than the raw text: the module's docstrings
        legitimately *name* the registry read as the caller's job, and a
        substring grep cannot tell prose from an import.
        """
        import ast
        import inspect

        import agent.output_router as mod

        tree = ast.parse(inspect.getsource(mod))
        imported: set[str] = set()
        called: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name:
                    called.add(name)

        forbidden_modules = {"bridge.poll_registry", "popoto.redis_db", "redis"}
        assert not (imported & forbidden_modules), (
            f"output_router imports {imported & forbidden_modules} — the decision "
            "function must stay pure; the executor performs the read and passes "
            "has_open_question in"
        )
        assert "session_has_open_poll" not in called, (
            "output_router calls session_has_open_poll — that read belongs to the "
            "executor, not the decision function"
        )


class TestRouteSessionOutputThreadsOpenQuestion:
    def test_keyword_reaches_the_decision_function(self):
        from agent.output_router import route_session_output

        action, _cap = route_session_output(
            msg="Which approach?",
            stop_reason="end_turn",
            auto_continue_count=0,
            session_type="eng",
            classification_type="sdlc",
            has_open_question=True,
        )
        assert action == "pause_open_question"

    def test_default_preserves_nudge_continue(self):
        from agent.output_router import route_session_output

        action, _cap = route_session_output(
            msg="Which approach?",
            stop_reason="end_turn",
            auto_continue_count=0,
            session_type="eng",
            classification_type="sdlc",
        )
        assert action == "nudge_continue"
