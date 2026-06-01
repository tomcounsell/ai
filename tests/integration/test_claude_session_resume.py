"""Live integration test: ctrl-c a real Claude session, then resume it.

Proves the `--resume` / session_id capability against a real `claude`
subprocess: a session is given a codeword, interrupted with SIGINT (exactly
what ctrl-c sends), and then resumed -- and must still recall the codeword,
demonstrating that `claude --resume <session_id>` preserves conversation
context across the interruption.

**Skipped by default.** Spends real Max-subscription tokens. Enable with:

    GRANITE_LIVE=1 pytest tests/integration/test_claude_session_resume.py -v -m slow
"""

from __future__ import annotations

import os
import shutil
import signal
import time

import pytest

from agent.claude_session import ClaudeSession, ClaudeSessionConfig

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _result_text(events: list[dict]) -> str:
    for ev in events:
        if ev.get("type") == "result" and isinstance(ev.get("result"), str):
            return ev["result"]
    return ""


@pytest.mark.skipif(
    os.environ.get("GRANITE_LIVE") != "1" or shutil.which("claude") is None,
    reason="live resume spike: set GRANITE_LIVE=1 with `claude` (OAuth) available.",
)
def test_ctrl_c_then_resume_preserves_context():
    model = os.environ.get("GRANITE_GAME_MODEL", "haiku")
    session = ClaudeSession(ClaudeSessionConfig(model=model, cwd=os.getcwd()))
    session.start()
    try:
        # Turn 1: plant a codeword and let the session id get captured.
        session.send_message(
            "Remember this codeword for later: VELVET-7391. Reply with only the word 'ok'."
        )
        session.read_until_result(timeout=120)
        captured = session.session_id
        assert captured, "session_id should be captured from the stream-json init event"

        # Simulate ctrl-c: SIGINT to the child, then confirm it exits.
        os.kill(session.pid, signal.SIGINT)
        for _ in range(50):
            if not session.is_running:
                break
            time.sleep(0.1)
        assert not session.is_running, "session should exit after SIGINT (ctrl-c)"

        # Resume the SAME session and prove context survived.
        assert session.resume() is True
        assert session.session_id == captured
        session.send_message(
            "What codeword did I ask you to remember? Reply with only the codeword."
        )
        events = session.read_until_result(timeout=120)
        assert "VELVET-7391" in _result_text(events)
    finally:
        session.stop()
