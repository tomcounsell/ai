"""Tests for Container's pkill-fallback gating on pool-owned PTY pairs.

`_run_pkill_fallback` runs `pkill -f "claude --permission-mode
bypassPermissions"` — a machine-wide pattern that matches every pool
slot's prewarmed pair, every concurrent granite session's pair, and any
operator-owned interactive `claude` session. With the PTYPool in
production, the fallback firing at the end of EVERY container run would
kill all of those. The gate: pool-backed containers (prewarmed pair
passed to the ctor) must never invoke pkill; the self-spawned path
(tests, ping-pong) keeps the safety net.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from agent.granite_container.container import Container


def _mock_pty(
    idle_buffer: str = "[/complete] done\nbypass permissions\n❯ ",
    session_id: str = "mock-session-00000000",
) -> MagicMock:
    pty = MagicMock()
    result = MagicMock()
    result.saw_idle = True
    result.buffer = idle_buffer
    # Per-turn capture (level-triggered idle, PR #1612): the container
    # classifies `turn_buffer or buffer`, so mirror the buffer here.
    result.turn_buffer = idle_buffer
    result.idle_marker = "bypass permissions"
    result.elapsed_ms = 1
    pty.read_until_idle.return_value = result
    pty.isalive.return_value = True
    pty.last_resume_uuid.return_value = None
    # Session ID needed so _transcript_path() returns non-None, enabling
    # last_assistant_text to be called via the container's zero-LLM path.
    pty._session_id = session_id
    return pty


class TestUsesPoolPair(unittest.TestCase):
    def test_true_with_prewarmed_pair(self) -> None:
        c = Container(user_message="hi", pm_pty=_mock_pty(), dev_pty=_mock_pty())
        self.assertTrue(c._uses_pool_pair())

    def test_false_without_prewarmed_pair(self) -> None:
        c = Container(user_message="hi")
        self.assertFalse(c._uses_pool_pair())

    def test_false_with_partial_pair(self) -> None:
        c = Container(user_message="hi", pm_pty=_mock_pty())
        self.assertFalse(c._uses_pool_pair())


class TestPkillGating(unittest.TestCase):
    def test_pool_backed_run_never_invokes_pkill(self) -> None:
        pm = _mock_pty(session_id="mock-session-pm")
        dev = _mock_pty(session_id="mock-session-dev")
        pm._released_to_pool = True
        dev._released_to_pool = True
        c = Container(user_message="hi", pm_pty=pm, dev_pty=dev)

        def _lat_stub(path, *, mtime_before=None):
            if not path or "mock-session-dev" in path:
                return ""
            return "[/complete]\nDone."

        with (
            patch("agent.granite_container.container.subprocess.run") as sub_run,
            patch(
                "agent.granite_container.container.last_assistant_text",
                side_effect=_lat_stub,
            ),
        ):
            result = c.run()
        sub_run.assert_not_called()
        self.assertEqual(result.exit_reason, "pm_complete")

    def test_self_spawned_fallback_still_invokes_pkill(self) -> None:
        c = Container(user_message="hi")
        with patch("agent.granite_container.container.subprocess.run") as sub_run:
            c._run_pkill_fallback()
        sub_run.assert_called_once()
        pattern = sub_run.call_args[0][0]
        self.assertIn("pkill", pattern[0])

    def test_pool_backed_fallback_is_noop_even_called_directly(self) -> None:
        c = Container(user_message="hi", pm_pty=_mock_pty(), dev_pty=_mock_pty())
        with patch("agent.granite_container.container.subprocess.run") as sub_run:
            c._run_pkill_fallback()
        sub_run.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
