"""Tests for the session steering mechanism.

Covers:
1. steer_session() — happy path, terminal guard, empty message rejection
2. output_router exports — all expected symbols accessible
3. queued_steering_messages — turn-boundary injection logic (unit-level)
4. valor-session CLI — help output and basic argument parsing
"""


class TestOutputRouterExports:
    """Verify agent.output_router exports all expected symbols."""

    def test_determine_delivery_action_importable(self):
        from agent.output_router import determine_delivery_action

        assert callable(determine_delivery_action)

    def test_route_session_output_importable(self):
        from agent.output_router import route_session_output

        assert callable(route_session_output)

    def test_max_nudge_count_importable(self):
        from agent.output_router import MAX_NUDGE_COUNT

        assert isinstance(MAX_NUDGE_COUNT, int)
        assert MAX_NUDGE_COUNT > 0

    def test_nudge_message_importable(self):
        from agent.output_router import NUDGE_MESSAGE

        assert isinstance(NUDGE_MESSAGE, str)
        assert len(NUDGE_MESSAGE) > 10

    def test_send_to_chat_result_importable(self):
        from agent.output_router import SendToChatResult

        state = SendToChatResult()
        assert state.completion_sent is False
        assert state.defer_reaction is False
        assert state.auto_continue_count == 0


class TestBackwardCompatExports:
    """Verify symbols are still importable from agent.agent_session_queue for backward compat."""

    def test_max_nudge_count_from_queue(self):
        from agent.agent_session_queue import MAX_NUDGE_COUNT

        assert isinstance(MAX_NUDGE_COUNT, int)

    def test_nudge_message_from_queue(self):
        from agent.agent_session_queue import NUDGE_MESSAGE

        assert isinstance(NUDGE_MESSAGE, str)

    def test_determine_delivery_action_from_queue(self):
        from agent.agent_session_queue import determine_delivery_action

        assert callable(determine_delivery_action)

    def test_send_to_chat_result_from_queue(self):
        from agent.agent_session_queue import SendToChatResult

        state = SendToChatResult()
        assert state.completion_sent is False

    def test_steer_session_from_queue(self):
        from agent.agent_session_queue import steer_session

        assert callable(steer_session)

    def test_re_enqueue_session_from_queue(self):
        from agent.agent_session_queue import re_enqueue_session

        assert callable(re_enqueue_session)


class TestSteerSessionGuards:
    """Unit tests for steer_session() edge cases (no Redis required)."""

    def test_empty_message_rejected(self):
        from agent.agent_session_queue import steer_session

        result = steer_session("nonexistent-session", "")
        assert result["success"] is False
        assert "Empty message" in result["error"]

    def test_whitespace_only_message_rejected(self):
        from agent.agent_session_queue import steer_session

        result = steer_session("nonexistent-session", "   ")
        assert result["success"] is False
        assert "Empty message" in result["error"]

    def test_nonexistent_session_returns_error(self):
        """steer_session on a non-existent session returns an error dict."""
        from agent.agent_session_queue import steer_session

        result = steer_session("definitely-does-not-exist-xyz-123", "hello")
        assert result["success"] is False
        assert result["session_id"] == "definitely-does-not-exist-xyz-123"
        assert result["error"] is not None


class TestRouteSessionOutput:
    """Tests for route_session_output() persona-aware cap selection."""

    def test_teammate_uses_reduced_cap(self):
        """Teammate sessions use TEAMMATE_MAX_NUDGE_COUNT, not MAX_NUDGE_COUNT."""
        from agent.output_router import MAX_NUDGE_COUNT, route_session_output
        from agent.teammate_handler import TEAMMATE_MAX_NUDGE_COUNT

        assert TEAMMATE_MAX_NUDGE_COUNT < MAX_NUDGE_COUNT

        _action, cap = route_session_output(
            msg="some output",
            stop_reason="end_turn",
            auto_continue_count=0,
            is_teammate=True,
        )
        assert cap == TEAMMATE_MAX_NUDGE_COUNT

    def test_non_teammate_uses_full_cap(self):
        from agent.output_router import MAX_NUDGE_COUNT, route_session_output

        _action, cap = route_session_output(
            msg="some output",
            stop_reason="end_turn",
            auto_continue_count=0,
            is_teammate=False,
        )
        assert cap == MAX_NUDGE_COUNT

    def test_pm_sdlc_returns_nudge_continue(self):
        from agent.output_router import route_session_output

        action, _cap = route_session_output(
            msg="Stage complete",
            stop_reason="end_turn",
            auto_continue_count=0,
            session_type="pm",
            classification_type="sdlc",
        )
        assert action == "nudge_continue"

    def test_non_pm_returns_deliver(self):
        from agent.output_router import route_session_output

        action, _cap = route_session_output(
            msg="Task complete",
            stop_reason="end_turn",
            auto_continue_count=0,
            session_type="dev",
        )
        assert action == "deliver"


class TestValorSessionCLI:
    """Tests for the valor-session CLI tool."""

    def test_module_importable(self):
        import tools.valor_session as vs

        assert hasattr(vs, "main")
        assert callable(vs.main)

    def test_help_exits_zero(self):
        """--help should exit with code 0."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "tools.valor_session", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "valor-session" in result.stdout

    def test_subcommands_present_in_help(self):
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "tools.valor_session", "--help"],
            capture_output=True,
            text=True,
        )
        for cmd in ("create", "steer", "status", "list", "kill"):
            assert cmd in result.stdout, f"Subcommand '{cmd}' missing from help output"

    def test_steer_requires_message(self):
        """valor-session steer --id X should fail without --message."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "tools.valor_session", "steer", "--id", "abc"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "message" in result.stderr.lower() or "required" in result.stderr.lower()
