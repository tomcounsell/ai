"""Tests for `PTYDriver.read_until_idle`'s optional per-iteration callback.

Gap B (#1843): `on_pty_read` liveness previously fired only once per
`_cycle_idle` return (a full `read_until_idle` call), not per inner poll
tick. A wedge *inside* a long idle-path turn left `last_pty_read_loop_at`
stale until the whole call returned. `read_until_idle` now accepts an
optional `on_read_iteration` callback invoked once per inner poll
iteration, so callers can sample liveness far more often.

These tests mirror the mocking pattern already established in
`test_pty_driver.py::TestReadUntilIdle` / `TestLevelTriggeredIdle`: a
mocked pexpect child yields a fixed list of chunks, then raises
`pexpect.TIMEOUT` forever. `QUIESCENCE_S` is patched to 0 so the idle
gate resolves without a real-time busy-wait.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import pexpect

from agent.granite_container.pty_driver import PTYDriver

IDLE_FRAME = "⏺ ready\n❯ \nbypass permissions on (shift+tab to cycle)\n"


def _driver_with_mock(chunks: list[str]) -> PTYDriver:
    """Build a driver whose pexpect child yields `chunks` in order, then
    raises `pexpect.TIMEOUT` forever (mirrors `TestReadUntilIdle`)."""
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


@patch("agent.granite_container.pty_driver.QUIESCENCE_S", 0)
class TestReadUntilIdlePerIterationCallback(unittest.TestCase):
    def test_callback_fires_once_per_inner_poll_iteration(self) -> None:
        driver = _driver_with_mock([IDLE_FRAME])
        calls: list[str] = []

        result = driver.read_until_idle(
            min_content_bytes=0, timeout_s=2.0, on_read_iteration=calls.append
        )

        self.assertTrue(result.saw_idle, f"expected idle; buffer={result.buffer!r}")
        read_call_count = driver._child.read_nonblocking.call_count
        self.assertGreaterEqual(read_call_count, 1)
        self.assertEqual(
            len(calls),
            read_call_count,
            "on_read_iteration must fire exactly once per inner poll iteration "
            "(once per PTY read attempt)",
        )

    def test_none_callback_is_byte_identical_to_no_param(self) -> None:
        driver_default = _driver_with_mock([IDLE_FRAME])
        driver_explicit_none = _driver_with_mock([IDLE_FRAME])

        result_default = driver_default.read_until_idle(min_content_bytes=0, timeout_s=2.0)
        result_explicit_none = driver_explicit_none.read_until_idle(
            min_content_bytes=0, timeout_s=2.0, on_read_iteration=None
        )

        self.assertEqual(result_default.saw_idle, result_explicit_none.saw_idle)
        self.assertEqual(result_default.buffer, result_explicit_none.buffer)
        self.assertEqual(result_default.turn_buffer, result_explicit_none.turn_buffer)
        self.assertEqual(result_default.idle_marker, result_explicit_none.idle_marker)

    def test_raising_callback_does_not_break_read_loop(self) -> None:
        driver = _driver_with_mock([IDLE_FRAME])
        call_count = {"n": 0}

        def _boom(_buffer: str) -> None:
            call_count["n"] += 1
            raise RuntimeError("on_read_iteration callback exploded")

        result = driver.read_until_idle(min_content_bytes=0, timeout_s=2.0, on_read_iteration=_boom)

        self.assertTrue(
            result.saw_idle,
            "a raising on_read_iteration callback must not abort the read loop",
        )
        self.assertGreaterEqual(
            call_count["n"], 2, "the raising callback should still be invoked each iteration"
        )


if __name__ == "__main__":
    unittest.main()
