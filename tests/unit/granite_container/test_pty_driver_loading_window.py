"""Tests for the LOADING_RE trailing-window fix in `read_until_idle`.

The PTY capture is an append-only stream: every spinner frame the TUI
ever painted stays in the accumulated buffer. Before the fix, the
loading-indicator negative (`LOADING_RE`) was searched against the WHOLE
buffer, so one historical "· Thinking…" frame blocked idle declaration
for the remainder of the call — the read always timed out with
saw_idle=False even after the model finished and the idle bar painted
(the PR #1612 live-run symptom). The fix searches only the trailing
`LOADING_TAIL_WINDOW` chars, which always cover the *current* frame.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import pexpect

from agent.granite_container.pty_driver import (
    LOADING_TAIL_WINDOW,
    PTYDriver,
)


def _driver_with_chunks(chunks: list[str]) -> PTYDriver:
    driver = PTYDriver(role="pm", timeout_s=2.0)
    mock_child = MagicMock()
    chunks_iter = iter(chunks)

    def read_nonblocking(size: int, timeout: float) -> str:
        try:
            return next(chunks_iter)
        except StopIteration:
            raise pexpect.TIMEOUT("mock timeout")

    mock_child.read_nonblocking.side_effect = read_nonblocking
    driver._child = mock_child
    return driver


class TestLoadingTailWindow(unittest.TestCase):
    def test_historical_spinner_does_not_block_idle(self) -> None:
        """A spinner frame early in the capture must not prevent idle
        once the response has pushed it past the tail window."""
        spinner_frame = "· Thinking…  (esc to interrupt)\n"
        response = "Here is the answer. " * 40  # ~800 chars, > tail window
        idle_frame = "bypass permissions on\n❯ \n"
        driver = _driver_with_chunks([spinner_frame, response, idle_frame])
        result = driver.read_until_idle(min_content_bytes=0, timeout_s=2.0)
        self.assertTrue(
            result.saw_idle,
            f"historical spinner blocked idle; tail={result.buffer[-120:]!r}",
        )

    def test_spinner_in_tail_still_blocks_idle(self) -> None:
        """A spinner in the CURRENT frame (inside the tail window) must
        keep blocking idle — that's the heuristic's whole purpose."""
        frame = "bypass permissions on\n❯ \n· Sprouting… (3s · esc to interrupt)\n"
        driver = _driver_with_chunks([frame])
        result = driver.read_until_idle(min_content_bytes=0, timeout_s=1.0)
        self.assertFalse(result.saw_idle, "active spinner must block idle")

    def test_idle_without_any_spinner_still_works(self) -> None:
        driver = _driver_with_chunks(["bypass permissions on\n", "❯ \n"])
        result = driver.read_until_idle(min_content_bytes=0, timeout_s=2.0)
        self.assertTrue(result.saw_idle)

    def test_tail_window_is_sane(self) -> None:
        """The window must comfortably cover a current spinner frame
        (~60 chars) without spanning a whole prior response."""
        self.assertGreaterEqual(LOADING_TAIL_WINDOW, 100)
        self.assertLessEqual(LOADING_TAIL_WINDOW, 2000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
